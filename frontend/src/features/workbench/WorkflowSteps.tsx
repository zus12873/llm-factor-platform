import { Steps } from "antd"
import { WORKFLOW_STEPS, type WorkflowView } from "./sessionView"

export function WorkflowSteps({ view }: { view: WorkflowView }) {
  return (
    <Steps
      direction="vertical"
      size="small"
      current={view.activeStep}
      status={view.status}
      items={WORKFLOW_STEPS.map((step) => ({ title: step.label }))}
    />
  )
}
