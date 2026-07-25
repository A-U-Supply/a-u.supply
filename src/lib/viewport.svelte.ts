/**
 * One shared "are we on a phone" signal.
 *
 * Components that need to *render* differently under 640px (not just style
 * differently) were each spinning up their own matchMedia listener. This is
 * one listener for the whole page, and it stays reactive across rotation.
 *
 * 640px is the house breakpoint — see src/styles/global.css.
 */
let phone = $state(false);

if (typeof window !== 'undefined') {
  const mq = window.matchMedia('(max-width: 640px)');
  phone = mq.matches;
  mq.addEventListener('change', (e) => (phone = e.matches));
}

/** True at ≤640px. Read it inside a component to stay reactive. */
export function isPhone(): boolean {
  return phone;
}
