// In-page steps of `teams.py recap-summary`, evaluated as `(<this file>)(step, arg)`.
//
// Every step is synchronous: Chrome throttles timers in a tab that is not in front (a
// `setTimeout` inside an evaluate stalled for over 60 s, measured 2026-10-20), so all waiting
// happens in Python between evaluates. The only elements any step clicks are one chat
// row of Teams' left navigation, the "View recap" button of one meeting card, the Recap's
// "AI summary" tab and a notes row's chevron; a row's own button is a recording timestamp and is
// never touched.
(function (step, arg) {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const viewport = () => document.querySelector('[data-tid=message-pane-list-viewport]');
  const tabNamed = name => [...document.querySelectorAll('[role=tab]')].find(t => clean(t.innerText) === name);
  const recapCards = label => [...document.querySelectorAll('[data-tid=chat-pane-item]')].filter(
    c => (c.innerText || '').includes(label) && recapButton(c));
  const recapButton = card => [...card.querySelectorAll('button')].find(b => clean(b.innerText) === 'View recap');
  // A section is its H2 up to the nearest ancestor holding the list. Never the first [role=tree]
  // on the page: that is Teams' left navigation.
  const section = (name, role) => {
    const h = [...document.querySelectorAll('h2')].find(x => clean(x.innerText) === name);
    let n = h && h.parentElement;
    while (n && !n.querySelector(role)) n = n.parentElement;
    return n && n.querySelector(role);
  };
  const notes = () => section('Meeting notes', '[role=treegrid]');
  const followUps = () => section('Follow-up tasks', '[role=tree]');
  const chevron = row => [...row.querySelectorAll('div')].find(
    d => d.children.length && [...d.children].every(c => c.tagName.toLowerCase() === 'svg'));
  // Sub-rows and follow-up items start with a "." glyph that is not part of the text.
  const ownText = (node, strip) => {
    const copy = node.cloneNode(true);
    for (const sub of copy.querySelectorAll(strip)) sub.remove();
    return clean(copy.innerText).replace(/^\.\s*/, '');
  };
  const level = node => Math.max(1, +(node.getAttribute('aria-level') || 1));
  // A chat row is a level-2 item of the left navigation tree, whose own text is the chat title.
  // A chat pinned under Favorites appears twice; both rows open the same chat.
  const chatRows = name => [...document.querySelectorAll('[role=treeitem][aria-level="2"]')].filter(
    r => clean(r.innerText) === name);

  switch (step) {
    case 'guard':
      window.open = function () { return null; };
      return true;
    // A tab that is not in front paints no frames, and Teams finishes a chat switch on a frame.
    case 'visible':
      return document.visibilityState === 'visible';
    // A freshly opened Teams tab is ready once it shows a chat or the named chat's row.
    case 'ready':
      return document.readyState === 'complete' &&
        (!!document.querySelector('[data-tid=chat-title]') || chatRows(arg).length > 0);
    case 'state': {
      const title = document.querySelector('[data-tid=chat-title]');
      return {chat: title ? clean(title.innerText) : null, recap_open: !!tabNamed('AI summary')};
    }
    case 'select_chat': {
      const rows = chatRows(arg);
      if (!rows.length) return 'rows=0';
      // Teams opens a chat on the row's mousedown, which element.click() never sends.
      const target = rows[0].querySelector('.fui-TreeItemLayout__main') || rows[0];
      for (const type of ['mousedown', 'mouseup', 'click'])
        target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, button: 0}));
      return 'clicked';
    }
    case 'find_card':
      return recapCards(arg).length;
    case 'scroll_up': {
      const v = viewport();
      if (!v) return null;
      v.scrollTop = Math.max(0, v.scrollTop - v.clientHeight);
      v.dispatchEvent(new Event('scroll'));
      return {top: v.scrollTop, height: v.scrollHeight};
    }
    case 'open_recap': {
      const cards = recapCards(arg);
      if (cards.length !== 1) return 'cards=' + cards.length;
      recapButton(cards[0]).click();
      return 'clicked';
    }
    case 'select_tab': {
      const tab = tabNamed('AI summary');
      if (!tab) return 'no-tab';
      tab.click();
      return 'clicked';
    }
    case 'sections':
      return {notes: !!notes(), follow_ups: !!followUps()};
    // A chevron is a toggle, so each row is clicked once only: a second click before React
    // commits the first would collapse it again. `expand_pending` counts clicked rows not yet
    // expanded, so Python waits for the commit before the next pass.
    case 'expand': {
      const grid = notes();
      if (!grid) return null;
      const clicked = window.__teamsRecapClicked = window.__teamsRecapClicked || new WeakSet();
      let toggled = 0, unreachable = 0;
      for (const row of grid.querySelectorAll('[role=row][aria-expanded=false]')) {
        if (clicked.has(row)) continue;
        const chev = chevron(row);
        if (!chev || chev.closest('button')) { unreachable++; continue; }
        clicked.add(row);
        chev.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        toggled++;
      }
      return {toggled, unreachable};
    }
    case 'expand_pending': {
      const grid = notes();
      const clicked = window.__teamsRecapClicked || new WeakSet();
      return grid ? [...grid.querySelectorAll('[role=row][aria-expanded=false]')].filter(r => clicked.has(r)).length : 0;
    }
    case 'extract': {
      const grid = notes(), tree = followUps();
      if (!grid || !tree) return null;
      const out = ['Meeting notes'];
      let heads = 0, subs = 0, badges = 0, items = 0;
      for (const row of grid.querySelectorAll('[role=row]')) {
        const text = ownText(row, 'button');
        if (!text) continue;
        // A bare number is a count badge, not a note (Agent Mail #993).
        if (/^\d+$/.test(text)) { badges++; continue; }
        const lvl = level(row);
        lvl === 1 ? heads++ : subs++;
        out.push('  '.repeat(lvl - 1) + '- ' + text);
      }
      out.push('', 'Follow-up tasks');
      for (const item of tree.querySelectorAll('[role=treeitem]')) {
        const text = ownText(item, '[role=treeitem],[role=group],button');
        if (!text) continue;
        items++;
        out.push('  '.repeat(level(item) - 1) + '- ' + text);
      }
      const collapsed = grid.querySelectorAll('[role=row][aria-expanded=false]').length;
      return {text: out.join('\n') + '\n', heads, subs, badges, items, collapsed};
    }
  }
  return 'unknown-step';
})
