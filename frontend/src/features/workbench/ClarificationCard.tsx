/**
 * The blocking-clarification card.
 *
 * The platform will not guess a metric definition, and this card is where that
 * refusal becomes a question. Two properties follow from that:
 *
 * - The options come from the backend's metric registry, never from a list kept
 *   here. A local copy would eventually offer an option the registry has since
 *   marked disputed.
 * - There is no "skip" or "use default". A default here would be the guess the
 *   whole design exists to avoid.
 */
import { Alert, Button, Radio, Space, Typography } from "antd"
import { useState } from "react"
import type { components } from "../../api/schema"

type Question = components["schemas"]["ClarificationQuestion"]

interface Props {
  questions: Question[]
  sessionVersion: number
  submitting?: boolean
  onResolve: (answers: Record<string, string>, expectedVersion: number) => void
}

export function ClarificationCard({
  questions,
  sessionVersion,
  submitting,
  onResolve,
}: Props) {
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const unanswered = questions.filter((q) => !answers[q.question_id])

  return (
    <Alert
      type="warning"
      showIcon
      message="需要你确认口径"
      description={
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            这些表述有多种口径，平台不会替你选。
          </Typography.Text>
          {questions.map((question) => (
            <div key={question.question_id}>
              <Typography.Text strong>{question.question}</Typography.Text>
              <div>
                <Radio.Group
                  aria-label={question.question}
                  value={answers[question.question_id]}
                  onChange={(event) =>
                    setAnswers((current) => ({
                      ...current,
                      [question.question_id]: event.target.value,
                    }))
                  }
                >
                  {(question.options ?? []).map((option) => (
                    <Radio key={option} value={option}>
                      {option}
                    </Radio>
                  ))}
                </Radio.Group>
              </div>
            </div>
          ))}
          <Button
            type="primary"
            size="small"
            loading={submitting}
            disabled={unanswered.length > 0}
            onClick={() => onResolve(answers, sessionVersion)}
          >
            确认口径
          </Button>
        </Space>
      }
    />
  )
}
