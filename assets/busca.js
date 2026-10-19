/* Motor de busca literal na minuta — compartilhado entre a Consulta e o Grafo.
   Roda no navegador sobre dados/minuta_rev05.js (window.MINUTA). Sem servidor.

   - Ignora acentos e maiúsculas por padrão ("seguranca viaria" acha "Segurança Viária").
   - Palavra inteira por padrão ("deverá" não acha "deverão"); use * como curinga: dever*.
   - Modos: expressão exata | todas as palavras (no mesmo artigo) | qualquer palavra.
   - Tipos de comando normativo (obrigação, permissão...) funcionam como filtro adicional. */
(function () {
  const M = window.MINUTA;
  if (!M) { console.error('dados/minuta_rev05.js não carregou'); return; }

  const COMANDOS = {
    obrigacao: { nome: 'Obrigações', exemplo: 'deverá, deve, é obrigatório', re: String.raw`(dever[áã]o|deverá|devem|deve|fica[m]? obrigad[oa]s?|é obrigat[óo]ri[oa]|s[ãa]o obrigat[óo]ri[oa]s|ser[áã]o? obrigat[óo]ri[oa]s?|obriga-se|obrigam-se)` },
    competencia: { nome: 'Competências', exemplo: 'compete, cabe, incumbe', re: String.raw`(compete|competir[áã]o?|competem|cabe|caber[áã]o?|incumbe|é responsável|ser[áã]o? responsáve(?:l|is))` },
    permissao: { nome: 'Permissões', exemplo: 'poderá, fica autorizado', re: String.raw`(?<!não )(poder[áã]o|poderá|podem|pode|fica[m]? autorizad[oa]s?|é facultad[oa]|fica facultad[oa]|faculta-se|é permitid[oa]|ser[áã]o? permitid[oa]s?|admite-se|admitir-se-[áa])` },
    proibicao: { nome: 'Proibições', exemplo: 'é vedado, não poderá', re: String.raw`(é vedad[oa]|fica[m]? vedad[oa]s?|vedad[oa]s?|vedação|é proibid[oa]|fica[m]? proibid[oa]s?|proíbe-se|não poder[áã]o|não poderá|não ser[áã]o? permitid[oa]s?|não se admite|não será admitid[oa])` },
    regulamentacao: { nome: 'Depende de regulamentação', exemplo: 'lei específica, regulamento', re: String.raw`(lei específica|leis específicas|lei municipal específica|regulamenta[çc][ãa]o|regulamentad[oa]s?|regulamento|ato do Poder Executivo|decreto municipal|ato normativo|legislação específica|legislação complementar)` },
    prazo: { nome: 'Prazos', exemplo: 'no prazo de, até 2030', re: String.raw`(no prazo (?:máximo )?de|prazo de \d+|em até \d+|até (?:o ano de )?\d{4}|anualmente|a cada \d+\s*(?:\(\w+\)\s*)?(?:anos|meses)|\d+\s*\([\p{L}\s]+\)\s*(?:dias|meses|anos))` },
  };
  const LETRA = String.raw`[\p{L}\p{N}]`;
  for (const k in COMANDOS) {
    COMANDOS[k].regex = new RegExp(`(?<!${LETRA})${COMANDOS[k].re}(?!${LETRA})`, 'giu');
    COMANDOS[k].cor = `var(--c-${k})`;
  }

  // ---------- normalização com mapa de posições ----------
  function semAcento(c) { return c.normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function normalizar(s) {
    let out = ''; const map = [];
    for (let i = 0; i < s.length; i++) {
      const n = semAcento(s[i]).toLowerCase();
      for (const ch of n) { out += ch; map.push(i); }
    }
    map.push(s.length);
    return { out, map };
  }
  function norm(s) { return semAcento(String(s)).toLowerCase(); }

  const artigos = M.artigos;
  const porNumero = new Map(artigos.map((a) => [a.n, a]));
  const nos = new Map(M.estrutura.map((e) => [e.id, e]));
  for (const a of artigos) {
    for (const d of a.dispositivos) d._n = normalizar(d.texto);
  }

  // ---------- consulta ----------
  function termosDe(q, modo) {
    q = q.trim();
    if (!q) return [];
    if (modo === 'frase') return [q.replace(/^"|"$/g, '')];
    const out = []; const rx = /"([^"]+)"|(\S+)/g; let m;
    while ((m = rx.exec(q))) out.push(m[1] || m[2]);
    return out;
  }
  function regexDoTermo(t, opts) {
    let base = opts.exato ? t : norm(t);
    base = base.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, `${LETRA}*`).replace(/\s+/g, '\\s+');
    const src = opts.inteira ? `(?<!${LETRA})${base}(?!${LETRA})` : base;
    return new RegExp(src, opts.exato ? 'gu' : 'giu');
  }

  /* opts: { q, modo: 'frase'|'todas'|'qualquer', inteira, exato, comandos:Set, escopo: id de nó ou '' }
     retorna { artigos: [{a, hits, total, porDisp: Map(i -> [[ini,fim,classe]]), cmd: {k:n}}], termos, total } */
  function buscar(opts) {
    const termos = termosDe(opts.q || '', opts.modo || 'frase');
    const regs = termos.map((t) => regexDoTermo(t, opts));
    const cmds = [...(opts.comandos || [])];
    const res = []; let total = 0;
    for (const a of artigos) {
      if (opts._so && a.n !== opts._so) continue;
      if (opts.escopo && !a.caminho.some((c) => c.id === opts.escopo)) continue;
      const porDisp = new Map(); const achouTermo = new Array(regs.length).fill(false);
      let hits = 0; const cmdQt = {};
      a.dispositivos.forEach((d, i) => {
        const fonte = opts.exato ? d.texto : d._n.out;
        const mapa = opts.exato ? null : d._n.map;
        const marcas = [];
        regs.forEach((re, ti) => {
          re.lastIndex = 0; let m;
          while ((m = re.exec(fonte))) {
            if (m[0].length === 0) { re.lastIndex++; continue; }
            const ini = mapa ? mapa[m.index] : m.index;
            const fim = mapa ? mapa[m.index + m[0].length] : m.index + m[0].length;
            marcas.push([ini, fim, 'termo']); achouTermo[ti] = true; hits++;
          }
        });
        for (const k of cmds) {
          const re = COMANDOS[k].regex; re.lastIndex = 0; let m;
          while ((m = re.exec(d.texto))) {
            marcas.push([m.index, m.index + m[0].length, 'cmd:' + k]);
            cmdQt[k] = (cmdQt[k] || 0) + 1;
          }
        }
        if (marcas.length) porDisp.set(i, marcas);
      });
      let ok;
      if (!regs.length) ok = true;
      else if (opts.modo === 'todas') ok = achouTermo.every(Boolean);
      else ok = achouTermo.some(Boolean);
      if (ok && cmds.length) ok = cmds.every((k) => cmdQt[k]);
      if (!regs.length && !cmds.length) ok = false;
      if (opts._so) ok = true;
      if (!ok) continue;
      total += hits;
      res.push({ a, hits, porDisp, cmd: cmdQt });
    }
    return { artigos: res, termos, total, temTermo: regs.length > 0 };
  }

  // ---------- render ----------
  function esc(v) {
    return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function realcar(texto, marcas) {
    if (!marcas || !marcas.length) return esc(texto);
    const ms = marcas.slice().sort((x, y) => x[0] - y[0] || (x[2] === 'termo' ? -1 : 1));
    let out = ''; let pos = 0;
    for (const [ini, fim, cls] of ms) {
      if (ini < pos) continue;
      out += esc(texto.slice(pos, ini));
      const trecho = esc(texto.slice(ini, fim));
      if (cls === 'termo') out += `<mark>${trecho}</mark>`;
      else { const k = cls.slice(4); out += `<mark class="cmd" style="--cor:${COMANDOS[k].cor}" title="${esc(COMANDOS[k].nome)}">${trecho}</mark>`; }
      pos = fim;
    }
    return out + esc(texto.slice(pos));
  }
  function rotuloDisp(d) {
    if (d.tipo === 'inciso') return d.rotulo + ' –';
    return d.rotulo;
  }
  function htmlDisp(d, marcas) {
    const r = rotuloDisp(d);
    return `<p class="disp ${d.tipo}">${r ? `<span class="rot">${esc(r)}</span> ` : ''}${realcar(d.texto, marcas)}</p>`;
  }
  function htmlCaminho(a) {
    return `<div class="caminho">${a.caminho.map((c) => `<span>${esc(c.rotulo)}${c.nome ? ' · ' + esc(tituloCaso(c.nome)) : ''}</span>`).join('')}</div>`;
  }
  function tituloCaso(s) {
    if (s !== s.toUpperCase()) return s;
    const minus = new Set(['da', 'de', 'do', 'das', 'dos', 'e', 'a', 'o', 'na', 'no', 'em', 'para', 'por', 'com', 'ao', 'aos', 'às', 'à']);
    return s.toLowerCase().split(' ').map((w, i) => (i > 0 && minus.has(w)) ? w : w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  }
  function rotuloArt(n) { return n < 10 ? `Art. ${n}º` : `Art. ${n}`; }
  function nomeNo(id) {
    if (id.startsWith('anexo-')) { const ax = (M.anexos || []).find((x) => x.id === id); return ax ? `${ax.rotulo}${ax.nome ? ' – ' + ax.nome : ''}` : id; }
    const n = nos.get(id); if (!n) return id;
    const partes = []; let cur = n;
    while (cur && cur.tipo !== 'raiz') { partes.unshift(cur.rotulo); cur = nos.get(cur.pai); }
    return partes.reverse().join(', ');
  }
  function htmlRefs(a) {
    const partes = [];
    if (a.remete.length) partes.push(`<span class="refs">Remete a: ${a.remete.map((n) => `<button class="link" data-art="${n}">${rotuloArt(n)}</button>`).join(', ')}</span>`);
    if (a.citado_por.length) partes.push(`<span class="refs">Citado por: ${a.citado_por.map((n) => `<button class="link" data-art="${n}">${rotuloArt(n)}</button>`).join(', ')}</span>`);
    if (a.remete_estrutura.length) partes.push(`<span class="refs">Remete a: ${a.remete_estrutura.map((id) => esc(nomeNo(id))).join('; ')}</span>`);
    if (a.anexos.length) partes.push(`<span class="refs">Anexos: ${a.anexos.map((k) => esc(nomeNo('anexo-' + k))).join('; ')}</span>`);
    if (a.leis.length) partes.push(`<span class="refs">Leis citadas: ${a.leis.map(esc).join('; ')}</span>`);
    return partes.join('');
  }
  function htmlTags(a) {
    return Object.keys(COMANDOS).filter((k) => a.comandos[k]).map((k) =>
      `<span class="tag" style="--cor:${COMANDOS[k].cor}">${esc(COMANDOS[k].nome)} ${a.comandos[k]}</span>`).join('');
  }

  // ---------- leitor de artigo (dialog) ----------
  let dlg = null; let marcasAtuais = null;
  function garantirDialog() {
    if (dlg) return dlg;
    dlg = document.createElement('dialog');
    dlg.className = 'leitor';
    dlg.innerHTML = `<div class="leitor-cab"><div><div class="art-num" id="leitor-titulo"></div><div id="leitor-caminho"></div></div>
      <div class="leitor-nav"><button class="btn-sec" id="leitor-ant" type="button" aria-label="Artigo anterior">‹</button>
      <button class="btn-sec" id="leitor-prox" type="button" aria-label="Próximo artigo">›</button>
      <button class="btn-sec" id="leitor-fechar" type="button">Fechar</button></div></div>
      <div class="leitor-corpo" id="leitor-corpo"></div>`;
    document.body.appendChild(dlg);
    dlg.querySelector('#leitor-fechar').addEventListener('click', () => dlg.close());
    dlg.addEventListener('click', (e) => {
      if (e.target === dlg) dlg.close();
      const b = e.target.closest('[data-art]'); if (b) abrirArtigo(Number(b.dataset.art));
    });
    return dlg;
  }
  function abrirArtigo(n, buscaOpts) {
    const a = porNumero.get(n); if (!a) return;
    const d = garantirDialog();
    if (buscaOpts !== undefined) marcasAtuais = buscaOpts;
    let porDisp = new Map();
    if (marcasAtuais) {
      const r = buscar({ ...marcasAtuais, escopo: '' , _so: n });
      const hit = r.artigos.find((x) => x.a.n === n); if (hit) porDisp = hit.porDisp;
    }
    d.querySelector('#leitor-titulo').textContent = a.rotulo;
    d.querySelector('#leitor-caminho').innerHTML = htmlCaminho(a);
    d.querySelector('#leitor-corpo').innerHTML =
      a.dispositivos.map((x, i) => htmlDisp(x, porDisp.get(i))).join('') +
      `<div class="art-rodape">${htmlRefs(a)}</div>`;
    d.querySelector('#leitor-ant').onclick = () => abrirArtigo(n - 1);
    d.querySelector('#leitor-prox').onclick = () => abrirArtigo(n + 1);
    d.querySelector('#leitor-ant').disabled = !porNumero.has(n - 1);
    d.querySelector('#leitor-prox').disabled = !porNumero.has(n + 1);
    if (!d.open) d.showModal();
    d.querySelector('.leitor-corpo').scrollTop = 0;
  }

  // ---------- estado na URL ----------
  function lerURL() {
    const p = new URLSearchParams(location.search);
    return {
      q: p.get('q') || '',
      modo: ['frase', 'todas', 'qualquer'].includes(p.get('modo')) ? p.get('modo') : 'frase',
      inteira: p.get('inteira') !== '0',
      exato: p.get('exato') === '1',
      comandos: new Set((p.get('cmd') || '').split(',').filter((k) => COMANDOS[k])),
      escopo: p.get('escopo') || '',
    };
  }
  function urlDe(opts, pagina) {
    const p = new URLSearchParams();
    if (opts.q) p.set('q', opts.q);
    if (opts.modo !== 'frase') p.set('modo', opts.modo);
    if (!opts.inteira) p.set('inteira', '0');
    if (opts.exato) p.set('exato', '1');
    if (opts.comandos.size) p.set('cmd', [...opts.comandos].join(','));
    if (opts.escopo) p.set('escopo', opts.escopo);
    const s = p.toString();
    return (pagina || location.pathname) + (s ? '?' + s : '');
  }

  window.Busca = {
    M, COMANDOS, artigos, porNumero, nos, buscar, esc, realcar, htmlDisp, htmlCaminho, htmlRefs, htmlTags,
    tituloCaso, rotuloArt, nomeNo, abrirArtigo, lerURL, urlDe, norm,
  };
})();
