import bisect
import contextlib
import datetime as dt
import re
import time
from collections import OrderedDict
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pymysql  # type: ignore[import-untyped]

from factor_platform.settings import get_settings
from factor_platform.wind.connection import (
    TRANSIENT_MYSQL_ERROR_CODES as _TRANSIENT_MYSQL_ERROR_CODES,
)
from factor_platform.wind.connection import (
    WindConnectionFactory,
)

# Module-level connection factory singleton. Lazily built from Settings on
# first use (via ``_get_factory()``), or explicitly injected via
# ``configure_factory(...)`` for tests / explicit ops configuration.
# Constructing/importing the module does NOT touch ``_FACTORY`` — no socket,
# no credentials, no Settings read at import time.
_FACTORY: WindConnectionFactory | None = None

WIND_CONN = None
MISSING_INFO: list[dict[str, str]] = []
_TRADING_DATES_CACHE: dict[str, list[str]] = {}
_INDUSTRY_CODE_CACHE: dict[str, dict[str, Any]] = {}
_INSTRUMENT_CACHE: dict[str, dict[str, Any] | None] = {}
_LATEST_ADJFACTOR_CACHE: dict[str, float | None] = {}
_PRICE_CACHE_MAXSIZE = 16
_PRICE_CACHE: OrderedDict[Any, Any] = OrderedDict()
_QUERY_MAX_ATTEMPTS = 2


def add_missing_info(function, item, detail):
    MISSING_INFO.append({"function": function, "item": item, "detail": detail})


def configure_factory(factory: WindConnectionFactory) -> None:
    """Inject a connection factory.

    Closes any connection opened by the previous factory so the next
    ``query_df``/``init`` call opens a fresh connection from ``factory``.
    Used by tests and by ops code that constructs ``Settings`` explicitly
    instead of relying on the env-driven default.
    """
    global _FACTORY
    close_wind_conn()
    _FACTORY = factory


def _get_factory() -> WindConnectionFactory:
    global _FACTORY
    if _FACTORY is None:
        _FACTORY = WindConnectionFactory(get_settings())
    return _FACTORY


def get_wind_conn():
    global WIND_CONN
    if WIND_CONN is None or not getattr(WIND_CONN, "open", False):
        WIND_CONN = _get_factory().connect()
    return WIND_CONN


def close_wind_conn():
    global WIND_CONN
    if WIND_CONN is not None and getattr(WIND_CONN, "open", False):
        WIND_CONN.close()
    WIND_CONN = None


def query_df(sql, params=None):
    for attempt in range(_QUERY_MAX_ATTEMPTS):
        try:
            conn = get_wind_conn()
            with conn.cursor() as cursor:
                cursor.execute(sql, params or {})
                rows = cursor.fetchall()
            return pd.DataFrame(rows)
        except (pymysql.err.InterfaceError, pymysql.err.OperationalError) as error:
            error_code = error.args[0] if error.args else None
            if error_code not in _TRANSIENT_MYSQL_ERROR_CODES or attempt + 1 >= _QUERY_MAX_ATTEMPTS:
                raise
            close_wind_conn()
            time.sleep(0.25)


def _in_clause(values, prefix):
    params = {f"{prefix}{i}": v for i, v in enumerate(values)}
    clause = "(" + ",".join(f"%({k})s" for k in params) + ")"
    return clause, params


def _to_yyyymmdd(value):
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date, pd.Timestamp)):
        return pd.Timestamp(value).strftime("%Y%m%d")
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    s = str(value)
    if s.isdigit() and len(s) == 8:
        return s
    return pd.to_datetime(s).strftime("%Y%m%d")


def _to_timestamp_from_yyyymmdd(value):
    return pd.to_datetime(str(value), format="%Y%m%d")


def _to_date_from_yyyymmdd(value):
    return dt.datetime.strptime(str(value), "%Y%m%d").date()


def _format_date_string(value):
    if value is None or pd.isna(value):
        return "0000-00-00"
    s = str(value)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _as_list(order_book_ids):
    if isinstance(order_book_ids, str):
        return [order_book_ids], True
    return list(order_book_ids), False


def rq_to_wind(code):
    if code is None:
        return None
    code = str(code)
    if code.endswith(".XSHG"):
        return code[:-5] + ".SH"
    if code.endswith(".XSHE"):
        return code[:-5] + ".SZ"
    if code.endswith(".XBSE"):
        return code[:-5] + ".BJ"
    return code


def wind_to_rq(code):
    if code is None:
        return None
    return str(code)


def _wind_series_to_rq(values):
    return values.astype(str)


def _market_exchanges(market="cn"):
    if str(market or "cn").lower() == "hk":
        return ["HKEX"]
    return ["SSE", "SZSE"]


def _trading_day_strings(market="cn"):
    cache_key = str(market or "cn").lower()
    if cache_key not in _TRADING_DATES_CACHE:
        exchanges = _market_exchanges(cache_key)
        clause, params = _in_clause(exchanges, "exch")
        sql = f"""
            select distinct trade_days
            from asharecalendar
            where s_info_exchmarket in {clause}
            order by trade_days
        """
        df = query_df(sql, params)
        _TRADING_DATES_CACHE[cache_key] = [str(value) for value in df["trade_days"].tolist()]
    return _TRADING_DATES_CACHE[cache_key]


try:
    from rqdatac.services.basic import Instrument as RQInstrument  # type: ignore[import-not-found]
except Exception:

    class RQInstrument:  # type: ignore[no-redef]
        def __init__(self, data):
            self.__dict__ = data

        def __repr__(self):
            args = ", ".join(
                f"{k.lstrip('_')}={v!r}" for k, v in self.__dict__.items() if v is not None
            )
            return f"Instrument({args})"


def _board_type(row):
    code = str(row.get("s_info_code") or "")
    board_name = str(row.get("s_info_listboardname") or "")
    if code.startswith("688"):
        return "STAR"
    if code.startswith("300") or "创业" in board_name:
        return "GEM"
    if code.startswith("8") or code.startswith("4"):
        return "BSE"
    return "MainBoard"


def _float_or_none(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def _int_or_none(value):
    if value is None or pd.isna(value):
        return None
    return int(value)


def _make_instrument(row):
    wind_code = row.get("s_info_windcode")
    exchange = (
        "SH" if str(wind_code).endswith(".SH") else "SZ" if str(wind_code).endswith(".SZ") else "BJ"
    )
    pinyin = row.get("s_info_pinyin")
    data = {
        "order_book_id": wind_to_rq(wind_code),
        "industry_code": row.get("industry_code"),
        "market_tplus": 1,
        "symbol": row.get("s_info_name"),
        "special_type": "Normal",
        "exchange": exchange,
        "status": "Delisted" if row.get("is_delisted") else "Active",
        "type": "CS",
        "de_listed_date": _format_date_string(row.get("s_info_delistdate")),
        "listed_date": _format_date_string(row.get("s_info_listdate")),
        "sector_code_name": row.get("sector_code_name"),
        "abbrev_symbol": str(pinyin).upper() if pinyin else None,
        "sector_code": row.get("sector_code"),
        "round_lot": _int_or_none(row.get("round_lot")),
        "trading_hours": "09:31-11:30,13:01-15:00",
        "board_type": _board_type(row),
        "industry_name": row.get("industry_name"),
        "issue_price": _float_or_none(row.get("issue_price")),
        "trading_code": row.get("s_info_code"),
        "office_address": row.get("office_address"),
        "province": row.get("province"),
    }
    return RQInstrument(data)


def init(username=None, password=None, addr=("rqdatad-pro.ricequant.com", 16011), *_, **kwargs):
    query_df("select 1 as ok")
    return None


def instruments(order_book_ids, market="cn"):
    ids, single = _as_list(order_book_ids)
    missing_ids = [code for code in dict.fromkeys(ids) if code not in _INSTRUMENT_CACHE]
    if missing_ids:
        wind_codes = [rq_to_wind(code) for code in missing_ids]
        clause, params = _in_clause(wind_codes, "code")
        sql = f"""
            select d.s_info_windcode, d.s_info_code, d.s_info_name, d.s_info_exchmarket,
                   d.s_info_listdate, d.s_info_delistdate, d.s_info_listboard,
                   d.s_info_listboardname, d.is_delisted, d.s_info_pinyin,
                   csrc_code.industriesalias as industry_code,
                   csrc_code.industriesname as industry_name,
                   sector_code.wind_name_eng as sector_code,
                   sector_code.industriesname as sector_code_name,
                   ipo.s_ipo_price as issue_price,
                   intro.s_info_office as office_address,
                   intro.s_info_province as province,
                   custom.s_info_lot_size as round_lot
            from asharedescription d
            left join asharesecnindustriesclass csrc
              on d.s_info_windcode = csrc.s_info_windcode
             and csrc.cur_sign = '1'
            left join ashareindustriescode csrc_code
              on concat(substr(csrc.sec_ind_code, 1, 6), '0000000000') = csrc_code.industriescode
            left join ashareindustriesclass sector
              on d.s_info_windcode = sector.s_info_windcode
             and sector.cur_sign = '1'
            left join ashareindustriescode sector_code
              on concat(substr(sector.wind_ind_code, 1, 3), '0000000000000')
                 = sector_code.industriescode
            left join ashareipo ipo
              on d.s_info_windcode = ipo.s_info_windcode
            left join ashareintroductionzl intro
              on d.s_info_windcode = intro.s_info_windcode
            left join asharewindcustomcode custom
              on d.s_info_windcode = custom.s_info_windcode
            where d.s_info_windcode in {clause}
        """
        df = query_df(sql, params)
        fetched = {row["s_info_windcode"]: row for row in df.to_dict("records")}
        for code in missing_ids:
            _INSTRUMENT_CACHE[code] = fetched.get(rq_to_wind(code))

    result = [
        _make_instrument(_INSTRUMENT_CACHE[code].copy())
        if _INSTRUMENT_CACHE.get(code) is not None
        else None
        for code in ids
    ]
    return result[0] if single else result


def get_trading_dates(start_date, end_date, market="cn"):
    start = _to_yyyymmdd(start_date)
    end = _to_yyyymmdd(end_date)
    trading_days = _trading_day_strings(market)
    left = bisect.bisect_left(trading_days, start)
    right = bisect.bisect_right(trading_days, end)
    return [_to_date_from_yyyymmdd(value) for value in trading_days[left:right]]


def get_next_trading_date(date, n=1, market="cn"):
    if n == 0:
        return pd.to_datetime(date).date()
    if n < 0:
        return get_previous_trading_date(date, -n, market=market)
    date_str = _to_yyyymmdd(date)
    trading_days = _trading_day_strings(market)
    pos = bisect.bisect_right(trading_days, date_str)
    if pos >= len(trading_days):
        raise IndexError("single positional indexer is out-of-bounds")
    target = min(pos + int(n) - 1, len(trading_days) - 1)
    return _to_date_from_yyyymmdd(trading_days[target])


def get_previous_trading_date(date, n=1, market="cn"):
    if n == 0:
        return pd.to_datetime(date).date()
    if n < 0:
        return get_next_trading_date(date, -n, market=market)
    date_str = _to_yyyymmdd(date)
    trading_days = _trading_day_strings(market)
    pos = bisect.bisect_left(trading_days, date_str)
    if pos <= 0:
        raise IndexError("single positional indexer is out-of-bounds")
    target = max(pos - int(n), 0)
    return _to_date_from_yyyymmdd(trading_days[target])


def _date_index(start_date, end_date, market="cn"):
    dates = pd.to_datetime(get_trading_dates(start_date, end_date, market=market))
    return pd.DatetimeIndex(dates, name="date")


def is_st_stock(order_book_ids, start_date=None, end_date=None, market="cn"):
    ids, _ = _as_list(order_book_ids)
    display_ids = [wind_to_rq(rq_to_wind(code)) for code in ids]
    if start_date is None:
        instrument_list = [instruments(code) for code in ids]
        listed_dates = [inst.listed_date for inst in instrument_list if inst is not None]
        start_date = min(value for value in listed_dates if value != "0000-00-00")
    if end_date is None:
        end_date = dt.date.today()

    start = _to_yyyymmdd(start_date)
    end = _to_yyyymmdd(end_date)
    out = pd.DataFrame(
        False, index=_date_index(start, end, market=market), columns=display_ids, dtype=bool
    )

    wind_codes = [rq_to_wind(code) for code in ids]
    clause, params = _in_clause(wind_codes, "code")
    params.update({"start": start, "end": end})
    sql = f"""
        select s_info_windcode, s_type_st, entry_dt, remove_dt
        from asharest
        where s_info_windcode in {clause}
          and entry_dt <= %(end)s
          and (remove_dt is null or remove_dt > %(start)s)
          and s_type_st in ('S', 'Y', 'L')
    """
    df = query_df(sql, params)
    for row in df.itertuples(index=False):
        code = wind_to_rq(row.s_info_windcode)
        entry = pd.to_datetime(row.entry_dt, format="%Y%m%d")
        remove_raw = row.remove_dt
        remove = (
            pd.to_datetime(remove_raw, format="%Y%m%d")
            if pd.notna(remove_raw) and str(remove_raw)
            else pd.Timestamp.max
        )
        if code in out.columns:
            out.loc[(out.index >= entry) & (out.index < remove), code] = True
    return out


def is_suspended(order_book_ids, start_date=None, end_date=None, market="cn"):
    ids, _ = _as_list(order_book_ids)
    display_ids = [wind_to_rq(rq_to_wind(code)) for code in ids]
    if start_date is None:
        instrument_list = [instruments(code) for code in ids]
        listed_dates = [inst.listed_date for inst in instrument_list if inst is not None]
        start_date = min(value for value in listed_dates if value != "0000-00-00")
    if end_date is None:
        end_date = dt.date.today()

    start = _to_yyyymmdd(start_date)
    end = _to_yyyymmdd(end_date)
    out = pd.DataFrame(
        False, index=_date_index(start, end, market=market), columns=display_ids, dtype=bool
    )

    wind_codes = [rq_to_wind(code) for code in ids]
    clause, params = _in_clause(wind_codes, "code")
    params.update({"start": start, "end": end})

    sql_status = f"""
        select s_info_windcode, trade_dt, s_dq_tradestatuscode
        from ashareeodprices
        where s_info_windcode in {clause}
          and trade_dt between %(start)s and %(end)s
    """
    status = query_df(sql_status, params)

    sql_event = f"""
        select s_info_windcode, s_dq_suspenddate, s_dq_resumpdate
        from asharetradingsuspension
        where s_info_windcode in {clause}
          and s_dq_suspenddate <= %(end)s
          and (s_dq_resumpdate is null or s_dq_resumpdate > %(start)s)
    """
    events = query_df(sql_event, params)
    for row in events.itertuples(index=False):
        code = wind_to_rq(row.s_info_windcode)
        suspend = pd.to_datetime(row.s_dq_suspenddate, format="%Y%m%d")
        resump_raw = row.s_dq_resumpdate
        resump = (
            pd.to_datetime(resump_raw, format="%Y%m%d")
            if pd.notna(resump_raw) and str(resump_raw)
            else pd.to_datetime(end, format="%Y%m%d") + pd.Timedelta(days=1)
        )
        if code in out.columns:
            out.loc[(out.index >= suspend) & (out.index < resump), code] = True

    # Daily trading status is authoritative when present; it prevents stale
    # suspension rows with null resume dates from leaking past the resume day.
    if not status.empty:
        status_df = status.copy()
        status_df["order_book_id"] = _wind_series_to_rq(status_df["s_info_windcode"])
        status_df["date"] = pd.to_datetime(status_df["trade_dt"].astype(str), format="%Y%m%d")
        status_df["status_code"] = pd.to_numeric(status_df["s_dq_tradestatuscode"], errors="coerce")
        status_df = status_df.dropna(subset=["status_code"])
        if not status_df.empty:
            status_df["value"] = status_df["status_code"].astype(int) != -1
            status_df = status_df.drop_duplicates(["date", "order_book_id"], keep="last")
            status_wide = status_df.pivot(index="date", columns="order_book_id", values="value")
            status_wide = status_wide.reindex(index=out.index, columns=out.columns)
            out = out.mask(status_wide.notna(), status_wide).astype(bool)
    return out


PRICE_FIELD_MAP = {
    "open": "s_dq_open",
    "high": "s_dq_high",
    "low": "s_dq_low",
    "close": "s_dq_close",
    "prev_close": "s_dq_preclose",
    "volume": "s_dq_volume",
    "total_turnover": "s_dq_amount",
    "limit_up": "s_dq_limit",
    "limit_down": "s_dq_stopping",
}
ADJUSTED_PRICE_FIELD_MAP = {
    "open": "s_dq_adjopen",
    "high": "s_dq_adjhigh",
    "low": "s_dq_adjlow",
    "close": "s_dq_adjclose",
    "prev_close": "s_dq_adjpreclose",
}
FORWARD_ADJUSTED_PRICE_FIELD_MAP = {
    "open": "s_dq_adjopen",  # keep existing if no dedicated forward OHLC columns
    "high": "s_dq_adjhigh",
    "low": "s_dq_adjlow",
    "close": "s_dq_adjclose_backward",
    "prev_close": "s_dq_adjpreclose",
}
LIMIT_FIELDS = {"limit_up", "limit_down"}
PRE_ADJUST_TYPES = {"pre", "pre_volume"}
POST_ADJUST_TYPES = {"post", "post_volume"}
ADJUSTED_CLOSE_TYPES = PRE_ADJUST_TYPES | POST_ADJUST_TYPES
INDEX_PRICE_FIELD_MAP = {
    field: source for field, source in PRICE_FIELD_MAP.items() if field not in LIMIT_FIELDS
}


def _empty_price_frame(fields_list):
    index = pd.MultiIndex.from_arrays([[], []], names=["order_book_id", "date"])
    return pd.DataFrame(columns=fields_list, index=index, dtype=float)


def _price_cache_get(cache_key):
    if cache_key not in _PRICE_CACHE:
        return None
    result = _PRICE_CACHE[cache_key]
    _PRICE_CACHE.move_to_end(cache_key)
    return result.copy()


def _price_cache_set(cache_key, result):
    _PRICE_CACHE[cache_key] = result.copy()
    _PRICE_CACHE.move_to_end(cache_key)
    while len(_PRICE_CACHE) > _PRICE_CACHE_MAXSIZE:
        _PRICE_CACHE.popitem(last=False)


def _query_eod_prices(table, wind_codes, start, end, select_cols):
    if not wind_codes:
        return pd.DataFrame(columns=select_cols)
    clause, params = _in_clause(wind_codes, "code")
    params.update({"start": start, "end": end})
    sql = f"""
        select {", ".join(select_cols)}
        from {table}
        where s_info_windcode in {clause}
          and trade_dt between %(start)s and %(end)s
        order by s_info_windcode, trade_dt
    """
    df = query_df(sql, params)
    if df.empty:
        return pd.DataFrame(columns=select_cols)
    return df


def _looks_like_index_code(code):
    wind_code = rq_to_wind(code)
    if not wind_code or "." not in wind_code:
        return False
    security_code, exchange = wind_code.rsplit(".", 1)
    exchange = exchange.upper()
    if exchange in {"WI", "CI", "CSI"}:
        return True
    if exchange == "SH":
        return security_code.startswith(("000", "999"))
    if exchange == "SZ":
        return security_code.startswith("399")
    return False


def _numeric_column(frame, column):
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _latest_adjfactors(wind_codes):
    missing_codes = [
        code for code in dict.fromkeys(wind_codes) if code not in _LATEST_ADJFACTOR_CACHE
    ]
    if not missing_codes:
        return {code: _LATEST_ADJFACTOR_CACHE.get(code) for code in wind_codes}

    fetched = {}
    trading_days = _trading_day_strings("cn")
    today = dt.date.today().strftime("%Y%m%d")
    right = bisect.bisect_right(trading_days, today)
    recent_days = trading_days[max(0, right - 5) : right]
    if recent_days:
        code_clause, code_params = _in_clause(missing_codes, "code")
        date_clause, date_params = _in_clause(recent_days, "dt")
        sql = f"""
            select s_info_windcode, trade_dt, s_dq_adjfactor
            from ashareeodprices
            where s_info_windcode in {code_clause}
              and trade_dt in {date_clause}
              and s_dq_adjfactor is not null
            order by s_info_windcode, trade_dt desc
        """
        params = {}
        params.update(code_params)
        params.update(date_params)
        recent = query_df(sql, params)
        for row in recent.to_dict("records"):
            code = row["s_info_windcode"]
            if code not in fetched:
                fetched[code] = float(row["s_dq_adjfactor"])

    for code in missing_codes:
        if code in fetched:
            continue
        sql = """
            select s_info_windcode, s_dq_adjfactor
            from ashareeodprices
            where s_info_windcode = %(code)s
              and s_dq_adjfactor is not null
            order by trade_dt desc
            limit 1
        """
        df = query_df(sql, {"code": code})
        if not df.empty:
            fetched[code] = float(df.iloc[0]["s_dq_adjfactor"])

    for code in missing_codes:
        _LATEST_ADJFACTOR_CACHE[code] = fetched.get(code)
    return {code: _LATEST_ADJFACTOR_CACHE.get(code) for code in wind_codes}


def get_price(
    order_book_ids,
    start_date=None,
    end_date=None,
    frequency="1d",
    fields=None,
    adjust_type="pre",
    skip_suspended=False,
    expect_df=True,
    time_slice=None,
    market="cn",
    **kwargs,
):
    if frequency != "1d":
        raise NotImplementedError(
            "Only daily frequency is implemented for this factor_analysis replica."
        )
    if time_slice is not None:
        raise NotImplementedError("time_slice is not implemented for this daily-data replica.")
    if not expect_df:
        add_missing_info(
            "get_price", "expect_df=False", "Replica still returns a pandas DataFrame."
        )

    ids, _ = _as_list(order_book_ids)
    start_date = start_date or dt.date.today()
    end_date = end_date or start_date
    start = _to_yyyymmdd(start_date)
    end = _to_yyyymmdd(end_date)

    if fields is None:
        fields_list = ["open", "high", "low", "close", "volume", "total_turnover"]
    elif isinstance(fields, str):
        fields_list = [fields]
    else:
        fields_list = list(fields)

    unsupported = [field for field in fields_list if field not in PRICE_FIELD_MAP]
    if unsupported:
        raise NotImplementedError(f"Unsupported get_price fields: {unsupported}")

    cache_key = (
        tuple(ids),
        start,
        end,
        frequency,
        tuple(fields_list),
        adjust_type,
        bool(skip_suspended),
        bool(expect_df),
        str(market or "cn").lower(),
        tuple(sorted((str(key), repr(value)) for key, value in kwargs.items())),
    )
    cached = _price_cache_get(cache_key)
    if cached is not None:
        return cached

    wind_codes = [rq_to_wind(code) for code in ids]
    display_ids = [wind_to_rq(code) for code in wind_codes]
    use_adjusted_price = adjust_type in ADJUSTED_CLOSE_TYPES
    if adjust_type in PRE_ADJUST_TYPES:
        adj_map = FORWARD_ADJUSTED_PRICE_FIELD_MAP
    elif adjust_type in POST_ADJUST_TYPES:
        adj_map = ADJUSTED_PRICE_FIELD_MAP
    else:
        adj_map = {}

    stock_fields = []
    if use_adjusted_price:
        for field in fields_list:
            if field in adj_map:
                stock_fields.append(adj_map[field])
            else:
                stock_fields.append(PRICE_FIELD_MAP[field])
        if any(field in LIMIT_FIELDS for field in fields_list):
            stock_fields.extend(["s_dq_close", "s_dq_adjclose"])
    else:
        stock_fields = [PRICE_FIELD_MAP[field] for field in fields_list]

    hinted_index_codes = [code for code in wind_codes if _looks_like_index_code(code)]
    stock_codes = [code for code in wind_codes if code not in hinted_index_codes]

    stock_select_cols = ["s_info_windcode", "trade_dt", "s_dq_tradestatuscode"] + stock_fields
    stock_select_cols = list(dict.fromkeys(stock_select_cols))
    stock_raw = (
        _query_eod_prices("ashareeodprices", stock_codes, start, end, stock_select_cols)
        if stock_codes
        else pd.DataFrame(columns=stock_select_cols)
    )
    if not stock_raw.empty:
        stock_raw = stock_raw.copy()
        stock_raw["_source"] = "stock"

    found_stock_codes = (
        set(stock_raw["s_info_windcode"].astype(str).unique()) if not stock_raw.empty else set()
    )
    index_codes = list(
        dict.fromkeys(
            hinted_index_codes + [code for code in stock_codes if code not in found_stock_codes]
        )
    )
    index_fields = [
        INDEX_PRICE_FIELD_MAP[field] for field in fields_list if field in INDEX_PRICE_FIELD_MAP
    ]
    index_select_cols = ["s_info_windcode", "trade_dt"] + index_fields
    index_select_cols = list(dict.fromkeys(index_select_cols))
    index_raw = _query_eod_prices("aindexeodprices", index_codes, start, end, index_select_cols)
    if not index_raw.empty:
        index_raw = index_raw.copy()
        index_raw["_source"] = "index"

    raw = pd.concat([stock_raw, index_raw], ignore_index=True, sort=False)
    if raw.empty:
        result = _empty_price_frame(fields_list)
        _price_cache_set(cache_key, result)
        return result

    # NOTE: the two blocks below used to be guarded by ``if False and
    # adjust_type in {"pre", "pre_volume"}`` (dead code kept for documentation).
    # They are preserved as comments so the original limitation notes are not
    # lost; re-enable explicitly if/when pre-adjustment normalization is added.
    # add_missing_info(
    #     "get_price adjust_type='pre'",
    #     "复权因子归一化",
    #     "Replica normalizes by max s_dq_adjfactor inside the requested "
    #     "date range to avoid querying outside input dates.",
    # )
    # add_missing_info(
    #     "get_price adjust_type='pre'",
    #     "adjustment_factor_precision",
    #     "Uses Wind ashareeodprices.s_dq_adjfactor normalized by the latest "
    #     "available Wind factor; small differences vs RiceQuant can remain "
    #     "when their factor chains differ.",
    # )

    if skip_suspended:
        status = _numeric_column(raw, "s_dq_tradestatuscode")
        source = raw.get("_source", pd.Series("stock", index=raw.index))
        raw = raw.loc[source.eq("index") | status.isna() | (status == -1)].copy()
        if raw.empty:
            result = _empty_price_frame(fields_list)
            _price_cache_set(cache_key, result)
            return result

    order_rank = {code: i for i, code in enumerate(display_ids)}

    wind_code = raw["s_info_windcode"].astype(str)
    rq_code = _wind_series_to_rq(wind_code)
    df = pd.DataFrame(
        {
            "order_book_id": rq_code,
            "date": pd.to_datetime(raw["trade_dt"].astype(str), format="%Y%m%d"),
            "_order": rq_code.map(order_rank).fillna(10**9),
        },
        index=raw.index,
    )

    stock_mask = raw.get("_source", pd.Series("stock", index=raw.index)).eq("stock")
    for field in fields_list:
        source_field = PRICE_FIELD_MAP[field]
        values = _numeric_column(raw, source_field)
        if use_adjusted_price:
            if field in adj_map:
                adjusted_values = _numeric_column(raw, adj_map[field])
                values = values.mask(stock_mask, adjusted_values)
            elif field in LIMIT_FIELDS:
                close = _numeric_column(raw, "s_dq_close")
                adj_close = _numeric_column(raw, "s_dq_adjclose")
                ratio = adj_close.div(close.where(close != 0))
                values = values.mask(stock_mask, values * ratio)
        df[field] = values.astype(float)

    df = df.sort_values(["_order", "date"]).drop(columns="_order")
    result = df.set_index(["order_book_id", "date"])
    result.index = pd.MultiIndex.from_arrays(
        [result.index.get_level_values(0), pd.DatetimeIndex(result.index.get_level_values(1))],
        names=["order_book_id", "date"],
    )
    result = result[fields_list].astype(float)
    _price_cache_set(cache_key, result)
    return result


def index_components(
    order_book_id, date=None, start_date=None, end_date=None, return_create_tm=False, market="cn"
):
    def components_from_frame(raw, date_str):
        if raw.empty:
            stocks = []
            create_tm = pd.NaT
            return stocks, create_tm
        in_date = raw["s_con_indate"].astype(str)
        out_date = raw["s_con_outdate"].where(raw["s_con_outdate"].notna(), "99999999").astype(str)
        active = raw.loc[(in_date <= date_str) & (out_date >= date_str)]
        stocks = [
            wind_to_rq(value)
            for value in active.get("s_con_windcode", pd.Series(dtype=object)).tolist()
        ]
        create_tm = (
            pd.Timestamp(active["opdate"].max())
            if return_create_tm and len(active) and "opdate" in active
            else pd.NaT
        )
        return stocks, create_tm

    def component_events(start_str, end_str):
        sql = """
            select s_con_windcode, s_con_indate, s_con_outdate, opdate
            from aindexmembers
            where s_info_windcode = %(index_code)s
              and s_con_indate <= %(end)s
              and (s_con_outdate is null or s_con_outdate >= %(start)s)
            order by s_con_windcode
        """
        return query_df(
            sql, {"index_code": rq_to_wind(order_book_id), "start": start_str, "end": end_str}
        )

    if date is not None and (start_date is not None or end_date is not None):
        raise ValueError("date can not be used with start_date/end_date at the same time")

    if start_date is None and end_date is None:
        query_date = _to_yyyymmdd(date or dt.date.today())
        raw = component_events(query_date, query_date)
        stocks, create_tm = components_from_frame(raw, query_date)
        return (stocks, create_tm) if return_create_tm else stocks

    start = _to_yyyymmdd(start_date)
    end = _to_yyyymmdd(end_date)
    raw = component_events(start, end)
    result = {}
    for trading_date in get_trading_dates(start, end, market=market):
        date_str = trading_date.strftime("%Y%m%d")
        key = dt.datetime.combine(trading_date, dt.time())
        stocks, create_tm = components_from_frame(raw, date_str)
        result[key] = (stocks, create_tm) if return_create_tm else stocks
    return result


def _pad_industry_code(code):
    code = str(code)
    return code + "0" * max(0, 16 - len(code))


def _industry_code_records(codes):
    missing_codes = [code for code in dict.fromkeys(codes) if code not in _INDUSTRY_CODE_CACHE]
    if missing_codes:
        clause, params = _in_clause(missing_codes, "industry")
        sql = f"""
            select industriescode, industriesname, industriesalias, levelnum
            from ashareindustriescode
            where industriescode in {clause}
        """
        df = query_df(sql, params)
        fetched = {row["industriescode"]: row for row in df.to_dict("records")}
        for code in missing_codes:
            _INDUSTRY_CODE_CACHE[code] = fetched.get(code, {})
    return {code: _INDUSTRY_CODE_CACHE.get(code, {}) for code in codes}


def _industry_code_lookup(raw_code):
    raw = str(raw_code)
    first_code = _pad_industry_code(raw[:4])
    second_code = _pad_industry_code(raw[:6])
    full_code = _pad_industry_code(raw)
    codes = [first_code, second_code, full_code]
    by_code = _industry_code_records(codes)
    first = by_code.get(first_code, {})
    second = by_code.get(second_code, {})
    full = by_code.get(full_code, {})
    return {
        "first_code": first.get("industriesalias") or raw[:4],
        "first_name": first.get("industriesname") or raw[:4],
        "second_code": second.get("industriesalias") or raw[:6],
        "second_name": second.get("industriesname") or raw[:6],
        "full_code": full.get("industriesalias") or raw,
        "full_name": full.get("industriesname") or raw,
    }


def _industry_filters(
    order_book_ids=None, start_date=None, end_date=None, code_field="s_info_windcode"
):
    where = []
    params = {}
    if order_book_ids is not None:
        ids, _ = _as_list(order_book_ids)
        wind_codes = [rq_to_wind(code) for code in ids]
        clause, code_params = _in_clause(wind_codes, "industry_code")
        where.append(f"{code_field} in {clause}")
        params.update(code_params)
    if end_date is not None:
        params["end"] = _to_yyyymmdd(end_date)
        where.append("entry_dt <= %(end)s")
    if start_date is not None:
        params["start"] = _to_yyyymmdd(start_date)
        where.append("(remove_dt is null or remove_dt > %(start)s)")
    where_sql = " where " + " and ".join(where) if where else ""
    return where_sql, params


class WindClient:
    def execute(self, command, *args, **kwargs):
        order_book_ids = kwargs.get("order_book_ids")
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        if command == "__internal__zx2019_industry":
            where_sql, params = _industry_filters(order_book_ids, start_date, end_date)
            sql = f"""
                select s_info_windcode, citics_ind_code, entry_dt, remove_dt
                from ashareindustriesclasscitics
                {where_sql}
                order by s_info_windcode, entry_dt
            """
            df = query_df(sql, params)
            out = []
            for row in df.to_dict("records"):
                info = _industry_code_lookup(row["citics_ind_code"])
                out.append(
                    {
                        "order_book_id": wind_to_rq(row["s_info_windcode"]),
                        "start_date": pd.to_datetime(
                            row["entry_dt"], format="%Y%m%d"
                        ).to_pydatetime(),
                        "cancel_date": pd.to_datetime(
                            row["remove_dt"], format="%Y%m%d"
                        ).to_pydatetime()
                        if row.get("remove_dt")
                        else dt.datetime(2200, 12, 31),
                        "first_industry_code": info["first_code"],
                        "first_industry_name": info["first_name"],
                        "second_industry_code": info["second_code"],
                        "second_industry_name": info["second_name"],
                    }
                )
            return out

        if command == "__internal__shenwan_industry":
            where_sql, params = _industry_filters(order_book_ids, start_date, end_date)
            sql = f"""
                select s_info_windcode, sw_ind_code, entry_dt, remove_dt
                from ashareswnindustriesclass
                {where_sql}
                order by s_info_windcode, entry_dt
            """
            df = query_df(sql, params)
            out = []
            for row in df.to_dict("records"):
                info = _industry_code_lookup(row["sw_ind_code"])
                out.append(
                    {
                        "order_book_id": wind_to_rq(row["s_info_windcode"]),
                        "start_date": pd.to_datetime(
                            row["entry_dt"], format="%Y%m%d"
                        ).to_pydatetime(),
                        "cancel_date": pd.to_datetime(
                            row["remove_dt"], format="%Y%m%d"
                        ).to_pydatetime()
                        if row.get("remove_dt")
                        else dt.datetime(2200, 12, 31),
                        "version": 2,
                        "index_name": info["first_name"],
                        "index_code": f"{info['first_code']}.INDX",
                        "index_name2": info["second_name"],
                        "index_code2": f"{info['second_code']}.INDX",
                    }
                )
            add_missing_info(
                "__internal__shenwan_industry",
                "version",
                "ashareswnindustriesclass has no version field; replica sets version=2.",
            )
            return out

        raise NotImplementedError(f"WindClient.execute does not implement command: {command}")


class _ClientModule:
    @staticmethod
    def get_client():
        return WindClient()


client = _ClientModule()


class Factor:
    def __init__(self, name, transforms=None):
        self.name = name
        self.transforms = list(transforms or [])

    def with_transform(self, transform):
        return Factor(self.name, self.transforms + [transform])

    def __repr__(self):
        return f"Factor({self.name!r}, transforms={self.transforms!r})"


def LOG(factor):
    return factor.with_transform("log")


def execute_factor(factor, order_book_ids, start_date, end_date, *args, **kwargs):
    ids, _ = _as_list(order_book_ids)
    display_ids = [wind_to_rq(rq_to_wind(code)) for code in ids]
    if isinstance(factor, str):
        factor = Factor(factor)
    if factor.name != "market_cap_3":
        raise NotImplementedError("Only Factor('market_cap_3') is implemented.")

    start = _to_yyyymmdd(start_date)
    end = _to_yyyymmdd(end_date)
    wind_codes = [rq_to_wind(code) for code in ids]
    clause, params = _in_clause(wind_codes, "code")
    params.update({"start": start, "end": end})
    sql = f"""
        select s_info_windcode, trade_dt, s_val_mv
        from ashareeodderivativeindicator
        where s_info_windcode in {clause}
          and trade_dt between %(start)s and %(end)s
        order by s_info_windcode, trade_dt
    """
    raw = query_df(sql, params)
    dates = pd.DatetimeIndex(pd.to_datetime(get_trading_dates(start, end)), name="date")
    out = pd.DataFrame(index=dates, columns=display_ids, dtype=float)
    if not raw.empty:
        raw = raw.copy()
        raw["order_book_id"] = _wind_series_to_rq(raw["s_info_windcode"])
        raw["date"] = pd.to_datetime(raw["trade_dt"].astype(str), format="%Y%m%d")
        value = pd.to_numeric(raw["s_val_mv"], errors="coerce") * 10000
        if "log" in factor.transforms:
            value = np.log(value.where(value > 0))
        raw["value"] = value
        raw = raw.drop_duplicates(["date", "order_book_id"], keep="last")
        pivoted = raw.pivot(index="date", columns="order_book_id", values="value")
        out = pivoted.reindex(index=dates, columns=display_ids).astype(float)
    add_missing_info(
        "execute_factor(LOG(Factor('market_cap_3')))",
        "market_cap_3",
        "Uses ashareeodderivativeindicator.s_val_mv * 10000 as total market cap.",
    )
    return out


_GENERIC_QUERY_SHAPES = {
    "point_range",
    "report_period",
    "announcement_range",
    "interval_overlap",
    "static_lookup",
    "cross_section_asof",
}


def _safe_query_identifier(value, label):
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", text):
        raise ValueError(f"Invalid {label}: {value}")
    return text


def _generic_table_columns(table_name):
    schema = query_df(
        """
        select column_name
        from information_schema.columns
        where table_schema = database()
          and table_name = %(table_name)s
        """,
        {"table_name": table_name},
    )
    if schema.empty:
        raise ValueError(f"Unknown Wind table: {table_name}")
    column = "COLUMN_NAME" if "COLUMN_NAME" in schema.columns else "column_name"
    return {str(value).lower() for value in schema[column].tolist()}


def execute_generic_query_plan(plan):
    """Execute a backend-planned, schema-validated read-only Wind query."""
    if not isinstance(plan, dict):
        raise TypeError("plan must be a dict")
    table_name = _safe_query_identifier(plan.get("table_name"), "table_name")
    query_shape = str(plan.get("query_shape") or "")
    if query_shape not in _GENERIC_QUERY_SHAPES:
        raise ValueError(f"Unsupported query_shape: {query_shape}")

    selected_fields = [
        _safe_query_identifier(field, "selected_field")
        for field in plan.get("selected_fields") or []
    ]
    if not selected_fields:
        raise ValueError("selected_fields must not be empty")
    code_field = _safe_query_identifier(plan.get("code_field"), "code_field")
    order_book_ids, _ = _as_list(plan.get("order_book_ids"))
    if not order_book_ids:
        raise ValueError("order_book_ids must not be empty")

    role_fields = {}
    for role in (
        "observation_date",
        "report_period",
        "announcement_date",
        "interval_start",
        "interval_end",
    ):
        if plan.get(role):
            role_fields[role] = _safe_query_identifier(plan[role], role)

    required_roles = {
        "point_range": {"observation_date"},
        "report_period": {"report_period"},
        "announcement_range": {"announcement_date"},
        "interval_overlap": {"interval_start", "interval_end"},
        "static_lookup": set(),
        "cross_section_asof": {"observation_date"},
    }[query_shape]
    missing_roles = sorted(required_roles - set(role_fields))
    if missing_roles:
        raise ValueError(f"Query shape {query_shape} is missing roles: {missing_roles}")

    columns = _generic_table_columns(table_name)
    requested_columns = {code_field, *selected_fields, *role_fields.values()}
    unknown = sorted(requested_columns - columns)
    if unknown:
        raise ValueError(f"Unknown columns in {table_name}: {unknown}")

    wind_codes = [rq_to_wind(code) for code in order_book_ids]
    code_clause, params = _in_clause(wind_codes, "code")
    conditions = [f"{code_field} in {code_clause}"]
    start_value = plan.get("start_date")
    end_value = plan.get("end_date")
    if query_shape != "static_lookup":
        if start_value is None or end_value is None:
            raise ValueError(f"Query shape {query_shape} requires start_date and end_date")
        params.update(
            {
                "start": _to_yyyymmdd(start_value),
                "end": _to_yyyymmdd(end_value),
            }
        )

    if query_shape == "point_range":
        date_field = role_fields["observation_date"]
        conditions.append(f"{date_field} between %(start)s and %(end)s")
        order_fields = [code_field, date_field]
    elif query_shape == "report_period":
        period_field = role_fields["report_period"]
        conditions.append(f"{period_field} between %(start)s and %(end)s")
        announcement_field = role_fields.get("announcement_date")
        if announcement_field and plan.get("as_of_date"):
            params["as_of"] = _to_yyyymmdd(plan["as_of_date"])
            conditions.append(f"{announcement_field} <= %(as_of)s")
        order_fields = [code_field, period_field]
        if announcement_field:
            order_fields.append(announcement_field)
    elif query_shape == "announcement_range":
        date_field = role_fields["announcement_date"]
        conditions.append(f"{date_field} between %(start)s and %(end)s")
        order_fields = [code_field, date_field]
    elif query_shape == "interval_overlap":
        start_field = role_fields["interval_start"]
        end_field = role_fields["interval_end"]
        conditions.extend(
            [
                f"{start_field} <= %(end)s",
                (f"({end_field} is null or {end_field} = '' or {end_field} >= %(start)s)"),
            ]
        )
        order_fields = [code_field, start_field, end_field]
    elif query_shape == "cross_section_asof":
        date_field = role_fields["observation_date"]
        conditions.append(f"{date_field} <= %(end)s")
        order_fields = [code_field, date_field]
    else:
        order_fields = [code_field]

    select_columns = list(dict.fromkeys([code_field, *role_fields.values(), *selected_fields]))
    sql = (
        f"select {', '.join(select_columns)} "
        f"from {table_name} "
        f"where {' and '.join(conditions)} "
        f"order by {', '.join(order_fields)}"
    )
    raw = query_df(sql, params)
    if raw.empty:
        output_columns = [
            "order_book_id",
            *role_fields.keys(),
            *selected_fields,
            "source_table",
            "source_fields",
        ]
        return pd.DataFrame(columns=list(dict.fromkeys(output_columns)))

    raw = raw.copy()
    if (
        query_shape == "report_period"
        and plan.get("dedup_policy") == "latest_announcement"
        and role_fields.get("announcement_date")
    ):
        raw = raw.sort_values(
            [
                code_field,
                role_fields["report_period"],
                role_fields["announcement_date"],
            ]
        ).drop_duplicates(
            subset=[code_field, role_fields["report_period"]],
            keep="last",
        )
    raw["order_book_id"] = raw[code_field].map(wind_to_rq)
    for role, field in role_fields.items():
        text = raw[field].astype(str)
        if text.str.fullmatch(r"\d{8}").all():
            raw[role] = pd.to_datetime(text, format="%Y%m%d")
        else:
            raw[role] = pd.to_datetime(text, errors="coerce")
    for field in selected_fields:
        with contextlib.suppress(TypeError, ValueError):
            raw[field] = pd.to_numeric(raw[field])
    raw["source_table"] = table_name
    raw["source_fields"] = ",".join(selected_fields)
    output_columns = [
        "order_book_id",
        *role_fields.keys(),
        *selected_fields,
        "source_table",
        "source_fields",
    ]
    return raw[list(dict.fromkeys(output_columns))]


RQ_WIND_CAPABILITY_VERSION = 1
RQ_WIND_CAPABILITIES = {
    "init": {
        "kind": "lifecycle",
        "purpose": "初始化并验证 Wind MySQL 连接。",
        "asset_types": ["all"],
        "parameters": {
            "username": {
                "required": False,
                "default": None,
                "meaning": "兼容 rqdatac.init，不用于 Wind MySQL 登录。",
            },
            "password": {
                "required": False,
                "default": None,
                "meaning": "兼容 rqdatac.init，不用于 Wind MySQL 登录。",
            },
            "addr": {
                "required": False,
                "default": ("rqdatad-pro.ricequant.com", 16011),
                "meaning": "兼容参数，不改变 Wind MySQL 地址。",
            },
        },
        "source_dependencies": [],
        "exact_outputs": [],
        "semantic_outputs": [
            {"name": "connection_ready", "type": "none", "meaning": "连接验证成功时返回 None。"}
        ],
        "return_schema": {"kind": "none"},
        "constraints": ["由生成器自动调用，不作为用户数据字段匹配目标。"],
        "planner": "lifecycle",
        "examples": ["wind.init()"],
    },
    "instruments": {
        "kind": "data",
        "purpose": "获取 A 股证券基础信息、行业、上市日期、交易所、办公地址、省份等属性。",
        "asset_types": ["stock"],
        "parameters": {
            "order_book_ids": {"required": True, "meaning": "RQ/Wind 证券代码或代码列表。"},
            "market": {
                "required": False,
                "default": "cn",
                "meaning": "当前基础信息实现面向中国股票市场。",
            },
        },
        "source_dependencies": [
            {
                "table": "asharedescription",
                "fields": [
                    "s_info_windcode",
                    "s_info_code",
                    "s_info_name",
                    "s_info_exchmarket",
                    "s_info_listdate",
                    "s_info_delistdate",
                    "s_info_listboard",
                    "s_info_listboardname",
                    "is_delisted",
                    "s_info_pinyin",
                ],
            },
            {
                "table": "asharesecnindustriesclass",
                "fields": ["s_info_windcode", "sec_ind_code", "wind_ind_code", "cur_sign"],
            },
            {
                "table": "ashareindustriesclass",
                "fields": ["s_info_windcode", "wind_ind_code", "cur_sign"],
            },
            {
                "table": "ashareindustriescode",
                "fields": ["industriescode", "industriesname", "industriesalias", "wind_name_eng"],
            },
            {"table": "ashareipo", "fields": ["s_info_windcode", "s_ipo_price"]},
            {
                "table": "ashareintroductionzl",
                "fields": ["s_info_windcode", "s_info_office", "s_info_province"],
            },
            {"table": "asharewindcustomcode", "fields": ["s_info_windcode", "s_info_lot_size"]},
        ],
        "exact_outputs": [
            {
                "table": "asharedescription",
                "field": "s_info_windcode",
                "output": "order_book_id",
                "coverage": "exact",
            },
            {
                "table": "asharedescription",
                "field": "s_info_code",
                "output": "trading_code",
                "coverage": "exact",
            },
            {
                "table": "asharedescription",
                "field": "s_info_name",
                "output": "symbol",
                "coverage": "exact",
            },
            {
                "table": "asharedescription",
                "field": "s_info_listdate",
                "output": "listed_date",
                "coverage": "derived",
            },
            {
                "table": "asharedescription",
                "field": "s_info_delistdate",
                "output": "de_listed_date",
                "coverage": "derived",
            },
            {
                "table": "asharedescription",
                "field": "s_info_pinyin",
                "output": "abbrev_symbol",
                "coverage": "derived",
            },
            {
                "table": "ashareindustriescode",
                "field": "industriesname",
                "output": "industry_name",
                "coverage": "derived",
            },
            {
                "table": "ashareipo",
                "field": "s_ipo_price",
                "output": "issue_price",
                "coverage": "exact",
            },
            {
                "table": "ashareintroductionzl",
                "field": "s_info_office",
                "output": "office_address",
                "coverage": "exact",
            },
            {
                "table": "ashareintroductionzl",
                "field": "s_info_province",
                "output": "province",
                "coverage": "exact",
            },
            {
                "table": "asharewindcustomcode",
                "field": "s_info_lot_size",
                "output": "round_lot",
                "coverage": "derived",
            },
        ],
        "semantic_outputs": [
            {
                "name": "instrument",
                "type": "Instrument",
                "intents": [
                    "证券基础信息",
                    "股票基础信息",
                    "上市日期",
                    "证券名称",
                    "办公地址",
                    "省份",
                    "行业",
                ],
            },
        ],
        "return_schema": {
            "kind": "object_or_list",
            "item": "Instrument",
            "single_input_returns_single": True,
        },
        "constraints": ["输入必须是证券代码；当前不支持按名称模糊搜索。"],
        "planner": "instruments",
        "examples": ["wind.instruments(order_book_ids=['600519.SH'], market='cn')"],
    },
    "get_trading_dates": {
        "kind": "calendar",
        "purpose": "获取闭区间内的交易日列表。",
        "asset_types": ["stock", "index", "fund"],
        "parameters": {
            "start_date": {"required": True, "meaning": "起始日期。"},
            "end_date": {"required": True, "meaning": "结束日期。"},
            "market": {
                "required": False,
                "default": "cn",
                "meaning": "cn 使用 SSE/SZSE，hk 使用 HKEX。",
            },
        },
        "source_dependencies": [
            {"table": "asharecalendar", "fields": ["trade_days", "s_info_exchmarket"]}
        ],
        "exact_outputs": [],
        "semantic_outputs": [
            {
                "name": "trading_dates",
                "type": "list[date]",
                "intents": ["交易日列表", "交易日序列", "区间交易日"],
            },
        ],
        "return_schema": {"kind": "list", "item": "date"},
        "constraints": ["返回 start_date 与 end_date 闭区间内交易日。"],
        "planner": "calendar_range",
        "examples": [
            "wind.get_trading_dates(start_date='2026-07-01', end_date='2026-07-10', market='cn')"
        ],
    },
    "get_next_trading_date": {
        "kind": "calendar",
        "purpose": "获取基准日期之后第 N 个交易日。",
        "asset_types": ["stock", "index", "fund"],
        "parameters": {
            "date": {"required": True, "meaning": "基准日期。"},
            "n": {"required": False, "default": 1, "meaning": "向后偏移交易日数；0 返回基准日期。"},
            "market": {"required": False, "default": "cn", "meaning": "交易市场。"},
        },
        "source_dependencies": [
            {"table": "asharecalendar", "fields": ["trade_days", "s_info_exchmarket"]}
        ],
        "exact_outputs": [],
        "semantic_outputs": [
            {
                "name": "next_trading_date",
                "type": "date",
                "intents": ["后一个交易日", "下一个交易日", "向后交易日", "后N个交易日"],
            },
        ],
        "return_schema": {"kind": "scalar", "type": "date"},
        "constraints": ["n < 0 时内部转为 get_previous_trading_date。"],
        "planner": "calendar_offset",
        "examples": ["wind.get_next_trading_date(date='2026-07-01', n=1, market='cn')"],
    },
    "get_previous_trading_date": {
        "kind": "calendar",
        "purpose": "获取基准日期之前第 N 个交易日。",
        "asset_types": ["stock", "index", "fund"],
        "parameters": {
            "date": {"required": True, "meaning": "基准日期。"},
            "n": {"required": False, "default": 1, "meaning": "向前偏移交易日数；0 返回基准日期。"},
            "market": {"required": False, "default": "cn", "meaning": "交易市场。"},
        },
        "source_dependencies": [
            {"table": "asharecalendar", "fields": ["trade_days", "s_info_exchmarket"]}
        ],
        "exact_outputs": [],
        "semantic_outputs": [
            {
                "name": "previous_trading_date",
                "type": "date",
                "intents": ["前一个交易日", "上一个交易日", "向前交易日", "前N个交易日"],
            },
        ],
        "return_schema": {"kind": "scalar", "type": "date"},
        "constraints": ["n < 0 时内部转为 get_next_trading_date。"],
        "planner": "calendar_offset",
        "examples": ["wind.get_previous_trading_date(date='2026-07-01', n=1, market='cn')"],
    },
    "is_st_stock": {
        "kind": "status",
        "purpose": "判断股票在日期区间内是否处于 ST 风险警示状态。",
        "asset_types": ["stock"],
        "parameters": {
            "order_book_ids": {"required": True, "meaning": "股票代码列表。"},
            "start_date": {
                "required": False,
                "default": None,
                "meaning": "为空时使用输入证券最早上市日。",
            },
            "end_date": {"required": False, "default": None, "meaning": "为空时使用当天。"},
            "market": {"required": False, "default": "cn", "meaning": "交易市场。"},
        },
        "source_dependencies": [
            {
                "table": "asharest",
                "fields": ["s_info_windcode", "s_type_st", "entry_dt", "remove_dt"],
            },
            {"table": "asharecalendar", "fields": ["trade_days", "s_info_exchmarket"]},
        ],
        "exact_outputs": [],
        "semantic_outputs": [
            {
                "name": "is_st",
                "type": "DataFrame[bool]",
                "intents": ["是否ST", "ST状态", "ST股", "风险警示状态"],
            },
        ],
        "return_schema": {
            "kind": "dataframe",
            "index": ["date"],
            "columns": "order_book_id",
            "dtype": "bool",
        },
        "constraints": ["s_type_st 仅是内部依赖；本函数不返回原始 ST 类型代码。"],
        "planner": "status",
        "examples": [
            "wind.is_st_stock(order_book_ids=['600519.SH'], "
            "start_date='2026-07-01', end_date='2026-07-10', market='cn')"
        ],
    },
    "is_suspended": {
        "kind": "status",
        "purpose": "判断股票在日期区间内是否停牌。",
        "asset_types": ["stock"],
        "parameters": {
            "order_book_ids": {"required": True, "meaning": "股票代码列表。"},
            "start_date": {
                "required": False,
                "default": None,
                "meaning": "为空时使用输入证券最早上市日。",
            },
            "end_date": {"required": False, "default": None, "meaning": "为空时使用当天。"},
            "market": {"required": False, "default": "cn", "meaning": "交易市场。"},
        },
        "source_dependencies": [
            {
                "table": "ashareeodprices",
                "fields": ["s_info_windcode", "trade_dt", "s_dq_tradestatuscode"],
            },
            {
                "table": "asharetradingsuspension",
                "fields": ["s_info_windcode", "s_dq_suspenddate", "s_dq_resumpdate"],
            },
            {"table": "asharecalendar", "fields": ["trade_days", "s_info_exchmarket"]},
        ],
        "exact_outputs": [],
        "semantic_outputs": [
            {
                "name": "is_suspended",
                "type": "DataFrame[bool]",
                "intents": ["是否停牌", "停牌状态", "停牌股票"],
            },
        ],
        "return_schema": {
            "kind": "dataframe",
            "index": ["date"],
            "columns": "order_book_id",
            "dtype": "bool",
        },
        "constraints": ["交易状态和停复牌日期仅是内部依赖；本函数不返回原始状态代码或事件日期。"],
        "planner": "status",
        "examples": [
            "wind.is_suspended(order_book_ids=['600519.SH'], "
            "start_date='2026-07-01', end_date='2026-07-10', market='cn')"
        ],
    },
    "get_price": {
        "kind": "data",
        "purpose": "获取股票或 A 股指数日频行情。",
        "asset_types": ["stock", "index"],
        "parameters": {
            "order_book_ids": {"required": True, "meaning": "股票或指数代码列表。"},
            "start_date": {
                "required": False,
                "default": None,
                "meaning": "起始日期；为空时使用当天。",
            },
            "end_date": {
                "required": False,
                "default": None,
                "meaning": "结束日期；为空时等于起始日期。",
            },
            "frequency": {"required": False, "default": "1d", "meaning": "当前只支持日频 1d。"},
            "fields": {"required": False, "default": None, "meaning": "行情输出字段列表。"},
            "adjust_type": {
                "required": False,
                "default": "pre",
                "meaning": "none 原始、pre 前复权、post 后复权。",
            },
            "skip_suspended": {"required": False, "default": False, "meaning": "是否删除停牌日。"},
            "expect_df": {
                "required": False,
                "default": True,
                "meaning": "兼容参数，始终返回 DataFrame。",
            },
            "time_slice": {"required": False, "default": None, "meaning": "当前不支持。"},
            "market": {"required": False, "default": "cn", "meaning": "交易市场。"},
        },
        "source_dependencies": [
            {
                "table": "ashareeodprices",
                "fields": [
                    "s_info_windcode",
                    "trade_dt",
                    "s_dq_tradestatuscode",
                    "s_dq_open",
                    "s_dq_high",
                    "s_dq_low",
                    "s_dq_close",
                    "s_dq_preclose",
                    "s_dq_volume",
                    "s_dq_amount",
                    "s_dq_limit",
                    "s_dq_stopping",
                    "s_dq_adjopen",
                    "s_dq_adjhigh",
                    "s_dq_adjlow",
                    "s_dq_adjclose",
                    "s_dq_adjpreclose",
                ],
            },
            {
                "table": "aindexeodprices",
                "fields": [
                    "s_info_windcode",
                    "trade_dt",
                    "s_dq_open",
                    "s_dq_high",
                    "s_dq_low",
                    "s_dq_close",
                    "s_dq_preclose",
                    "s_dq_volume",
                    "s_dq_amount",
                ],
            },
        ],
        "exact_outputs": [
            {
                "tables": ["ashareeodprices", "aindexeodprices"],
                "field": "s_dq_open",
                "argument": {"fields": ["open"], "adjust_type": "none"},
                "output": "open",
                "coverage": "exact",
            },
            {
                "tables": ["ashareeodprices", "aindexeodprices"],
                "field": "s_dq_high",
                "argument": {"fields": ["high"], "adjust_type": "none"},
                "output": "high",
                "coverage": "exact",
            },
            {
                "tables": ["ashareeodprices", "aindexeodprices"],
                "field": "s_dq_low",
                "argument": {"fields": ["low"], "adjust_type": "none"},
                "output": "low",
                "coverage": "exact",
            },
            {
                "tables": ["ashareeodprices", "aindexeodprices"],
                "field": "s_dq_close",
                "argument": {"fields": ["close"], "adjust_type": "none"},
                "output": "close",
                "coverage": "exact",
            },
            {
                "tables": ["ashareeodprices", "aindexeodprices"],
                "field": "s_dq_preclose",
                "argument": {"fields": ["prev_close"], "adjust_type": "none"},
                "output": "prev_close",
                "coverage": "exact",
            },
            {
                "tables": ["ashareeodprices", "aindexeodprices"],
                "field": "s_dq_volume",
                "argument": {"fields": ["volume"]},
                "output": "volume",
                "coverage": "exact",
            },
            {
                "tables": ["ashareeodprices", "aindexeodprices"],
                "field": "s_dq_amount",
                "argument": {"fields": ["total_turnover"]},
                "output": "total_turnover",
                "coverage": "exact",
            },
            {
                "table": "ashareeodprices",
                "field": "s_dq_limit",
                "argument": {"fields": ["limit_up"], "adjust_type": "none"},
                "output": "limit_up",
                "coverage": "exact",
            },
            {
                "table": "ashareeodprices",
                "field": "s_dq_stopping",
                "argument": {"fields": ["limit_down"], "adjust_type": "none"},
                "output": "limit_down",
                "coverage": "exact",
            },
            {
                "table": "ashareeodprices",
                "field": "s_dq_adjopen",
                "argument": {"fields": ["open"], "adjust_type": "post"},
                "output": "open",
                "coverage": "exact",
            },
            {
                "table": "ashareeodprices",
                "field": "s_dq_adjhigh",
                "argument": {"fields": ["high"], "adjust_type": "post"},
                "output": "high",
                "coverage": "exact",
            },
            {
                "table": "ashareeodprices",
                "field": "s_dq_adjlow",
                "argument": {"fields": ["low"], "adjust_type": "post"},
                "output": "low",
                "coverage": "exact",
            },
            {
                "table": "ashareeodprices",
                "field": "s_dq_adjclose",
                "argument": {"fields": ["close"], "adjust_type": "post"},
                "output": "close",
                "coverage": "exact",
            },
            {
                "table": "ashareeodprices",
                "field": "s_dq_adjpreclose",
                "argument": {"fields": ["prev_close"], "adjust_type": "post"},
                "output": "prev_close",
                "coverage": "exact",
            },
        ],
        "semantic_outputs": [
            {
                "name": "daily_prices",
                "type": "DataFrame",
                "intents": ["行情", "价格", "收盘价", "成交量", "成交额"],
            }
        ],
        "return_schema": {
            "kind": "dataframe",
            "index": ["order_book_id", "date"],
            "columns": "requested fields",
            "dtype": "float",
        },
        "constraints": [
            "frequency 仅支持 1d。",
            "股票复权只作用于 open/high/low/close/prev_close；"
            "volume 和 total_turnover 保持 Wind 原始字段。",
            "指数行情来自 aindexeodprices，不区分复权价格。",
        ],
        "planner": "price",
        "examples": [
            "wind.get_price(order_book_ids=['600519.SH'], "
            "start_date='2026-07-01', end_date='2026-07-10', "
            "fields=['close', 'volume'], adjust_type='none', "
            "skip_suspended=False, market='cn')"
        ],
    },
    "index_components": {
        "kind": "membership",
        "purpose": "获取指数在单日或区间内有效成分股。",
        "asset_types": ["index"],
        "parameters": {
            "order_book_id": {"required": True, "meaning": "指数代码。"},
            "date": {"required": False, "default": None, "meaning": "单日查询日期。"},
            "start_date": {"required": False, "default": None, "meaning": "区间起始日期。"},
            "end_date": {"required": False, "default": None, "meaning": "区间结束日期。"},
            "return_create_tm": {
                "required": False,
                "default": False,
                "meaning": "是否同时返回创建时间。",
            },
            "market": {"required": False, "default": "cn", "meaning": "区间展开使用的交易市场。"},
        },
        "source_dependencies": [
            {
                "table": "aindexmembers",
                "fields": [
                    "s_info_windcode",
                    "s_con_windcode",
                    "s_con_indate",
                    "s_con_outdate",
                    "opdate",
                ],
            },
            {"table": "asharecalendar", "fields": ["trade_days", "s_info_exchmarket"]},
        ],
        "exact_outputs": [],
        "semantic_outputs": [
            {
                "name": "active_components",
                "type": "list_or_dict",
                "intents": ["指数成分", "成分股", "指数成员"],
            },
        ],
        "return_schema": {
            "kind": "list_or_mapping",
            "single": "list[order_book_id]",
            "range": "dict[datetime, list[order_book_id]]",
        },
        "constraints": [
            "date 与 start_date/end_date 互斥；函数返回有效成分，不返回原始进出日期事件行。"
        ],
        "planner": "index_components",
        "examples": [
            "wind.index_components(order_book_id='000300.SH', "
            "start_date='2026-07-01', end_date='2026-07-10', "
            "return_create_tm=False, market='cn')"
        ],
    },
    "execute_factor": {
        "kind": "factor",
        "purpose": "执行 rq_wind_replica 已实现的内置因子表达式。",
        "asset_types": ["stock"],
        "parameters": {
            "factor": {
                "required": True,
                "meaning": "当前仅支持 Factor('market_cap_3')，可应用 LOG。",
            },
            "order_book_ids": {"required": True, "meaning": "股票代码列表。"},
            "start_date": {"required": True, "meaning": "起始日期。"},
            "end_date": {"required": True, "meaning": "结束日期。"},
        },
        "source_dependencies": [
            {
                "table": "ashareeodderivativeindicator",
                "fields": ["s_info_windcode", "trade_dt", "s_val_mv"],
            },
            {"table": "asharecalendar", "fields": ["trade_days", "s_info_exchmarket"]},
        ],
        "exact_outputs": [],
        "semantic_outputs": [
            {
                "name": "market_cap_3",
                "type": "DataFrame[float]",
                "intents": ["market_cap_3", "总市值因子", "市值暴露"],
                "source_formula": "s_val_mv * 10000",
            },
            {
                "name": "log_market_cap_3",
                "type": "DataFrame[float]",
                "intents": ["对数市值", "市值取对数"],
                "source_formula": "log(s_val_mv * 10000)",
            },
        ],
        "return_schema": {
            "kind": "dataframe",
            "index": ["date"],
            "columns": "order_book_id",
            "dtype": "float",
        },
        "constraints": ["当前仅支持 market_cap_3；LOG 是唯一登记的变换。"],
        "planner": "factor",
        "examples": [
            "wind.execute_factor(wind.LOG(wind.Factor('market_cap_3')), "
            "order_book_ids=['600519.SH'], start_date='2026-07-01', "
            "end_date='2026-07-10')"
        ],
        "factor_expressions": {
            "market_cap_3": {"constructor": "wind.Factor('market_cap_3')", "transforms": ["log"]},
        },
    },
    "execute_generic_query_plan": {
        "kind": "data",
        "purpose": "执行后端根据 Wind 表结构和业务字段角色生成的受控通用查询计划。",
        "asset_types": ["stock", "index", "fund"],
        "parameters": {
            "plan": {
                "required": True,
                "meaning": (
                    "后端生成的结构化查询计划；运行时会重新校验表名、字段名、"
                    "查询形状和实际 Schema。"
                ),
            },
        },
        "source_dependencies": [],
        "exact_outputs": [],
        "semantic_outputs": [
            {
                "name": "generic_wind_fields",
                "type": "DataFrame",
                "intents": ["通用 Wind 字段取数", "专用函数未覆盖字段"],
            },
        ],
        "return_schema": {
            "kind": "dataframe",
            "index": ["order_book_id", "business_time_roles"],
            "columns": "requested Wind fields",
        },
        "constraints": [
            "只接受后端支持的查询形状。",
            "所有表名和字段名必须通过运行时 information_schema 校验。",
            "不接受任意 SQL。",
        ],
        "planner": "generic_table",
        "examples": [
            "wind.execute_generic_query_plan(plan={"
            "'table_name': 'ashareeodderivativeindicator', "
            "'selected_fields': ['s_val_pb_new'], "
            "'query_shape': 'point_range', "
            "'code_field': 's_info_windcode', "
            "'observation_date': 'trade_dt', "
            "'order_book_ids': ['000001.SZ'], "
            "'start_date': '2026-07-01', 'end_date': '2026-07-10'})",
        ],
    },
    "Factor": {
        "kind": "expression",
        "purpose": "构造 execute_factor 使用的已登记因子表达式。",
        "asset_types": ["stock"],
        "parameters": {
            "name": {"required": True, "meaning": "当前仅支持 market_cap_3。"},
            "transforms": {"required": False, "default": None, "meaning": "内部变换列表。"},
        },
        "source_dependencies": [],
        "exact_outputs": [],
        "semantic_outputs": [
            {"name": "factor_expression", "type": "Factor", "intents": ["因子表达式"]}
        ],
        "return_schema": {"kind": "expression", "type": "Factor"},
        "constraints": ["不是独立取数工具，只能作为 execute_factor 的参数。"],
        "planner": "expression",
        "examples": ["wind.Factor('market_cap_3')"],
    },
    "LOG": {
        "kind": "expression",
        "purpose": "为已登记 Factor 增加 log 变换。",
        "asset_types": ["stock"],
        "parameters": {"factor": {"required": True, "meaning": "Factor 表达式。"}},
        "source_dependencies": [],
        "exact_outputs": [],
        "semantic_outputs": [
            {"name": "log_factor_expression", "type": "Factor", "intents": ["对数变换", "LOG"]}
        ],
        "return_schema": {"kind": "expression", "type": "Factor"},
        "constraints": ["不是独立取数工具，只能嵌入 execute_factor。"],
        "planner": "expression",
        "examples": ["wind.LOG(wind.Factor('market_cap_3'))"],
    },
}


rqdatac_like = SimpleNamespace(
    init=init,
    instruments=instruments,
    get_next_trading_date=get_next_trading_date,
    get_previous_trading_date=get_previous_trading_date,
    get_trading_dates=get_trading_dates,
    is_st_stock=is_st_stock,
    is_suspended=is_suspended,
    get_price=get_price,
    index_components=index_components,
    client=client,
)
