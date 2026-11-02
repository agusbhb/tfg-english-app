export function sessionGuard(session) {
  return (to) => {
    if (to.meta.private && !session.state.authenticated)
      return { name: 'login' }
    if (to.meta.guest && session.state.authenticated) return { name: 'welcome' }
    return true
  }
}
