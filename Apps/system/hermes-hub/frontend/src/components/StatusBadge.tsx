interface Props {
  status: string
}

export function StatusBadge({ status }: Props) {
  const label = status === 'authentication_required' ? 'Authentication required' : status.replace('_', ' ')
  return <span className={`status status--${status}`}>{label}</span>
}
