import { Field } from './AiPrimitives';

export function ModelOption({ label, choices, value, onChange, numeric = false, disabled = false, placeholder = '留空使用模型默认值' }: {
  readonly label: string; readonly choices: readonly string[]; readonly value: string;
  readonly onChange: (value: string) => void; readonly numeric?: boolean;
  readonly disabled?: boolean; readonly placeholder?: string;
}) {
  return <Field label={label}>{choices.length
    ? <select className="field" disabled={disabled} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>{choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
    </select>
    : <input className="field" disabled={disabled} type={numeric ? 'number' : 'text'} value={value}
      placeholder={disabled ? '选择模型后可调整' : placeholder} onChange={(event) => onChange(event.target.value)} />}</Field>;
}
