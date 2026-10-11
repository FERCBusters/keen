// Keep the summaries and drawer off the page's initial loading path.
export function initContextHelp(load = () => import('./context-help.js')) {
  if (document.documentElement.dataset.contextHelpReady) return;
  document.documentElement.dataset.contextHelpReady = 'true';
  let pending;
  document.addEventListener('click', async (event) => {
    const trigger = event.target.closest?.('[data-help-topic]');
    if (
      !trigger ||
      event.defaultPrevented ||
      event.button !== 0 ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    if (!window.HTMLDialogElement?.prototype.showModal) return;
    event.preventDefault();
    if (pending) return;
    trigger.setAttribute('aria-busy', 'true');
    try {
      pending = load();
      const help = await pending;
      if (trigger.isConnected) help.openContextHelp(trigger);
    } catch {
      // Keep the ordinary documentation link usable if the optional module fails.
      trigger.removeAttribute('data-help-topic');
      trigger.textContent = 'Open Help (new tab)';
      trigger.target = '_blank';
      trigger.rel = 'noopener noreferrer';
    } finally {
      pending = null;
      trigger.removeAttribute('aria-busy');
    }
  });
}
