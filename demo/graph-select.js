const chevron = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m4 6 4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const check = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m3 8 3 3 7-7" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// Preserve the native select as the data boundary, including existing change handlers.
export function enhanceSelect(select) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'graph-select-trigger';
  button.id = `${select.id}-trigger`;
  button.setAttribute('role', 'combobox');
  button.setAttribute('aria-label', select.getAttribute('aria-label'));
  button.setAttribute('aria-haspopup', 'listbox');
  button.setAttribute('aria-expanded', 'false');
  const label = document.createElement('span');
  label.className = 'graph-select-label';
  button.append(label);
  button.insertAdjacentHTML('beforeend', chevron);
  const menu = document.createElement('div');
  menu.className = 'graph-select-menu';
  menu.id = `${select.id}-menu`;
  menu.setAttribute('popover', 'manual');
  menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-label', select.getAttribute('aria-label'));
  button.setAttribute('aria-controls', menu.id);
  select.after(button);
  document.body.append(menu);
  select.hidden = true;
  let open = false;
  let active = 0;
  let search = '';
  let lastTyped = 0;

  function close() {
    if (open) menu.hidePopover();
    open = false;
    button.setAttribute('aria-expanded', 'false');
    button.removeAttribute('aria-activedescendant');
  }
  function highlight(index) {
    const options = [...select.options];
    if (!options.length) return;
    active = (index + options.length) % options.length;
    for (const [i, row] of [...menu.children].entries()) row.classList.toggle('is-active', i === active);
    button.setAttribute('aria-activedescendant', `${select.id}-option-${active}`);
    menu.children[active]?.scrollIntoView({ block: 'nearest' });
  }
  function sync() {
    close();
    label.textContent = select.selectedOptions[0]?.textContent ?? '选择版本';
    button.title = label.textContent;
    button.disabled = select.disabled || !select.options.length;
    menu.replaceChildren();
    [...select.options].forEach((option, index) => {
      const row = document.createElement('div');
      row.className = 'graph-select-option';
      row.id = `${select.id}-option-${index}`;
      row.dataset.index = String(index);
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', String(index === select.selectedIndex));
      row.setAttribute('aria-disabled', String(option.disabled));
      const name = document.createElement('span');
      name.textContent = option.textContent;
      row.append(name);
      if (index === select.selectedIndex) row.insertAdjacentHTML('beforeend', check);
      menu.append(row);
    });
  }
  function show() {
    if (button.disabled) return;
    menu.showPopover();
    open = true;
    button.setAttribute('aria-expanded', 'true');
    const rect = button.getBoundingClientRect();
    const width = Math.min(innerWidth - 24, Math.max(rect.width, select.id === 'graph-version' ? 144 : 228));
    menu.style.width = `${width}px`;
    menu.style.left = `${Math.max(12, Math.min(rect.left, innerWidth - width - 12))}px`;
    menu.style.maxHeight = `${Math.min(280, Math.max(100, innerHeight - rect.bottom - 20))}px`;
    menu.style.top = `${rect.bottom + 6}px`;
    highlight(Math.max(0, select.selectedIndex));
  }
  function choose(index) {
    if (select.options[index]?.disabled) return;
    select.selectedIndex = index;
    sync();
    select.dispatchEvent(new Event('change', { bubbles: true }));
    button.focus();
  }
  button.addEventListener('click', () => open ? close() : show());
  button.addEventListener('keydown', event => {
    if (event.key === 'Escape' || event.key === 'Tab') { close(); return; }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) {
      event.preventDefault();
      if (!open) { show(); return; }
      if (event.key === 'Enter' || event.key === ' ') { choose(active); return; }
      highlight(event.key === 'Home' ? 0 : event.key === 'End' ? select.options.length - 1 : active + (event.key === 'ArrowDown' ? 1 : -1));
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
      search = Date.now() - lastTyped > 700 ? event.key : search + event.key;
      lastTyped = Date.now();
      if (!open) show();
      const index = [...select.options].findIndex(option => option.textContent.toLowerCase().startsWith(search.toLowerCase()));
      if (index >= 0) highlight(index);
    }
  });
  menu.addEventListener('click', event => {
    const row = event.target.closest('[data-index]');
    if (row) choose(Number(row.dataset.index));
  });
  document.addEventListener('pointerdown', event => {
    if (open && !button.contains(event.target) && !menu.contains(event.target)) close();
  });
  window.addEventListener('resize', close);
  select.addEventListener('change', sync);
  select.addEventListener('graph-select-sync', sync);
  new MutationObserver(sync).observe(select, { childList: true, subtree: true, attributes: true });
  sync();
}
