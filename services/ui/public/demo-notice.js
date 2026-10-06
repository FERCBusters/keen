// Optional deployment notice. Closing it never changes server-side expiry.
(async () => {
  try {
    const response = await fetch('/api/v1/auth/methods', {cache: 'no-store'});
    if (!response.ok) return;
    const {demo} = await response.json();
    if (!demo?.enabled) return;
    const end = new Date(demo.expires_at);
    const expired = () => Date.now() >= end.getTime();
    const key = `keen-demo-dismissed:${demo.expires_at}:${expired()}`;
    try { if (sessionStorage.getItem(key) === '1') return; } catch {}
    const banner = document.createElement('aside');
    banner.setAttribute('role', 'note');
    banner.id = 'keen-demo-notice';
    // Normal flow below the fixed navbar's body padding: no competing top:0 layer.
    banner.style.cssText = 'position:relative;display:flex;align-items:flex-start;gap:16px;background:#fff3cd;color:#332701;padding:12px 20px;border-bottom:1px solid #b58100;font:15px/1.5 system-ui';
    const content = document.createElement('div');
    content.style.cssText = 'flex:1;min-width:0;overflow-wrap:anywhere';
    const notice = document.createElement('strong');
    const update = () => { notice.textContent = expired()
      ? 'DEMO ENDED — access is closed and this instance is scheduled for deletion. '
      : `DEMO ONLY — ends ${end.toLocaleString()}. Use fictional data only; do not enter sensitive business information, personal data or credentials. `; };
    update();
    const timer = setInterval(update, 30000);
    content.append(notice);
    if (demo.consultation_url) {
      const target = new URL(demo.consultation_url);
      if (target.protocol === 'https:') {
        const link = document.createElement('a');
        link.href = target.href;
        link.textContent = 'Discuss a KEEN installation.';
        link.style.color = '#332701'; content.append(link);
      }
    }
    const close = document.createElement('button');
    close.type = 'button'; close.textContent = '×';
    close.setAttribute('aria-label', 'Dismiss demo notice');
    close.title = 'Dismiss for this tab';
    close.style.cssText = 'flex:none;min-width:44px;min-height:44px;border:1px solid #b58100;border-radius:8px;background:transparent;color:inherit;font:24px system-ui;cursor:pointer';
    close.addEventListener('click', () => {
      try { sessionStorage.setItem(key, '1'); } catch {}
      clearInterval(timer); banner.remove();
    });
    banner.append(content, close);
    document.body.prepend(banner);
  } catch { /* Metadata availability never affects server-side expiry enforcement. */ }
})();
