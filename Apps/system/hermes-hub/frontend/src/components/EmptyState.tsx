interface Props {
  title: string
  children: React.ReactNode
}

export function EmptyState({ title, children }: Props) {
  return <div className="empty-state"><span className="empty-state__mark">+</span><h3>{title}</h3><p>{children}</p></div>
}
