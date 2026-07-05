// Shared render logic for the lews digest site.
// index.html shows picks, rejects.html shows rejects — selected via
// <body data-kind="picks|rejects">.

const COLORS = {
  biorxiv:  { name: 'bioRxiv',  hue: '#d97706' },  // amber
  chemrxiv: { name: 'chemRxiv', hue: '#2563eb' },  // blue
  rcsb:     { name: 'RCSB',     hue: '#7c3aed' },  // purple
};

function renderNav(kind) {
  // build the source nav links, filtered to this page's kind
  const navSources = document.getElementById('nav-sources');
  if (!navSources) return;
  ['biorxiv', 'chemrxiv', 'rcsb'].forEach(src => {
    const a = document.createElement('a');
    a.href = `#${src}-${kind}`;
    a.className = `nav-link src-${src}`;
    a.textContent = COLORS[src].name;
    navSources.appendChild(a);
  });
}

function renderDigest(kind) {
  return fetch('data/digest.json')
    .then(r => r.json())
    .then(d => {
      const dateEl = document.getElementById('digest-date');
      if (dateEl) dateEl.textContent = d.date || '';
      const root = document.getElementById('rendered');
      root.innerHTML = '';
      d.sections.filter(s => s.kind === kind).forEach(sec => {
        const color = COLORS[sec.source] || { name: sec.source, hue: '#666' };
        const section = document.createElement('section');
        section.id = sec.id;
        section.className = `source-${sec.source}`;
        section.style.scrollMarginTop = '4rem';

        const head = document.createElement('div');
        head.className = 'section-head';
        head.style.borderLeftColor = color.hue;
        head.innerHTML =
          `<span class="src-badge" style="background:${color.hue}">${color.name}</span>` +
          `<span class="section-count">${sec.count} ${kind === 'picks' ? 'picked' : 'rejected'}</span>`;
        section.appendChild(head);

        const body = document.createElement('div');
        body.className = 'section-body';
        body.innerHTML = marked.parse(sec.markdown);
        // colour each H2 (paper title) with the source hue
        body.querySelectorAll('h2').forEach(h => {
          h.style.borderLeft = `3px solid ${color.hue}`;
          h.style.paddingLeft = '0.5rem';
        });
        makeAbstractsCollapsible(body);
        section.appendChild(body);
        root.appendChild(section);
      });
    })
    .catch(e => {
      document.getElementById('rendered').innerHTML =
        `<p>Could not load digest: ${e}. Run <code>./run.sh</code> then <code>./publish.py</code>.</p>`;
    });
}

document.addEventListener('DOMContentLoaded', () => {
  const kind = document.body.dataset.kind || 'picks';
  renderNav(kind);
  renderDigest(kind);
});

// ---- expandable abstracts ----
const ABSTRACT_PREVIEW = 180; // chars shown before the "more" button

function makeAbstractsCollapsible(root) {
  // marked.js renders `- abstract: ...` as <li>abstract: ...</li>
  root.querySelectorAll('li').forEach(li => {
    const text = li.textContent;
    if (!text.startsWith('abstract:')) return;
    const full = text.slice('abstract:'.length).trim();
    if (full.length <= ABSTRACT_PREVIEW) return; // short enough, leave as-is

    const preview = full.slice(0, ABSTRACT_PREVIEW).replace(/\s+\S*$/, '') + ' …';
    li.innerHTML = '';
    li.appendChild(document.createTextNode('abstract: '));

    const span = document.createElement('span');
    span.className = 'abstract';

    const short = document.createElement('span');
    short.className = 'abstract-short';
    short.textContent = preview;

    const long = document.createElement('span');
    long.className = 'abstract-long';
    long.textContent = full;
    long.style.display = 'none';

    const btn = document.createElement('button');
    btn.className = 'more-btn';
    btn.textContent = 'more';
    btn.addEventListener('click', () => {
      const expanded = long.style.display !== 'none';
      short.style.display = expanded ? 'inline' : 'none';
      long.style.display = expanded ? 'none' : 'inline';
      btn.textContent = expanded ? 'more' : 'less';
    });

    span.appendChild(short);
    span.appendChild(long);
    span.appendChild(btn);
    li.appendChild(span);
  });
}
