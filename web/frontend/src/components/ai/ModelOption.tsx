import { Field } from './AiPrimitives';

export function ModelOption({ label, choices, value, onChange, numeric = false }: {
  readonly label: string; readonly choices: readonly string[]; readonly value: string;
  readonly onChange: (value: string) => void; readonly numeric?: boolean;
}) {
  return <Field label={label}>{choices.length
    ? <select className="field" value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">模型默认值</option>{choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
    </select>
    : <input className="field" type={numeric ? 'number' : 'text'} value={value} placeholder="模型默认值" onChange={(event) => onChange(event.target.value)} />}</Field>;
}
