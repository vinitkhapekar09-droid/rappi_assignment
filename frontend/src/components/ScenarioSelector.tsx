import { SCENARIOS, type ScenarioId } from '../api/fixtures'

interface ScenarioSelectorProps {
  value: ScenarioId
  disabled: boolean
  onChange: (id: ScenarioId) => void
}

export function ScenarioSelector({ value, disabled, onChange }: ScenarioSelectorProps) {
  const current = SCENARIOS.find((scenario) => scenario.id === value)
  return (
    <div className="scenario">
      <label className="field field--inline">
        <span className="field__label">Demo scenario (mock adapter)</span>
        <select
          className="input"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value as ScenarioId)}
        >
          {SCENARIOS.map((scenario) => (
            <option key={scenario.id} value={scenario.id}>
              {scenario.label}
            </option>
          ))}
        </select>
      </label>
      {current ? <p className="scenario__description">{current.description}</p> : null}
    </div>
  )
}
