"""Extrai a minuta do Plano Diretor (Rev.05) de .docx para JSON estruturado.

Saída: dados/minuta_rev05.json e dados/minuta_rev05.js (window.MINUTA = {...})
       dados/minuta_rev05_chunks.jsonl (um chunk por artigo, pronto para ingestão RAG)

Estrutura reconhecida:
  TÍTULO > CAPÍTULO > Seção > Subseção > Art. > (caput, §, incisos, alíneas, itens)
A numeração automática do Word (Art. %1., incisos em romano, alíneas) é
reconstruída a partir do numbering.xml; a contagem de incisos reinicia a cada
caput/§, a de alíneas a cada inciso (convenção da LC 95/1998).
"""
import json, re, sys, unicodedata
from collections import defaultdict, Counter
import docx
from docx.oxml.ns import qn

SRC = sys.argv[1] if len(sys.argv) > 1 else 'minuta_rev05.docx'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'site/dados'

d = docx.Document(SRC)

# ---------- numeração ----------
numbering = d.part.numbering_part.element
absfmt = {}
for an in numbering.findall(qn('w:abstractNum')):
    aid = an.get(qn('w:abstractNumId'))
    for lvl in an.findall(qn('w:lvl')):
        il = lvl.get(qn('w:ilvl'))
        f = lvl.find(qn('w:numFmt')); t = lvl.find(qn('w:lvlText')); st = lvl.find(qn('w:start'))
        absfmt[(aid, il)] = (f.get(qn('w:val')) if f is not None else None,
                             t.get(qn('w:val')) if t is not None else '',
                             int(st.get(qn('w:val'))) if st is not None else 1)
num2abs = {n.get(qn('w:numId')): n.find(qn('w:abstractNumId')).get(qn('w:val'))
           for n in numbering.findall(qn('w:num'))}


def numinfo(p):
    pPr = p._p.pPr
    np_ = pPr.numPr if pPr is not None else None
    if np_ is None:
        s = p.style
        while s is not None:
            sp = s.element.pPr
            if sp is not None and sp.numPr is not None:
                np_ = sp.numPr; break
            s = s.base_style
    if np_ is None or np_.numId is None or str(np_.numId.val) == '0':
        return None
    il = np_.ilvl.val if np_.ilvl is not None else 0
    f = absfmt.get((num2abs.get(str(np_.numId.val)), str(il)))
    if not f:
        return None
    return {'fmt': f[0], 'txt': f[1], 'start': f[2]}


def ptext(p):
    out = []
    for el in p._p.iter():
        if el.tag == qn('w:t'):
            out.append(el.text or '')
        elif el.tag in (qn('w:tab'), qn('w:br')):
            out.append(' ')
    return re.sub(r'\s+', ' ', ''.join(out)).strip()


def roman(n):
    vals = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'),
            (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]
    s = ''
    for v, r in vals:
        while n >= v:
            s += r; n -= v
    return s


def art_label(n):
    return f'Art. {n}º' if n < 10 else f'Art. {n}'


def slug(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


# ---------- percurso ----------
LEVELS = ['titulo', 'capitulo', 'secao', 'subsecao']
LABEL_RE = {
    'titulo': re.compile(r'^T[ÍI]TULO\s+([IVXLC]+|[ÚU]NICO)\b', re.I),
    'capitulo': re.compile(r'^CAP[ÍI]TULO\s+([IVXLC]+|[ÚU]NICO)\b', re.I),
    'secao': re.compile(r'^SE[ÇC][ÃA]O\s+([IVXLC]+|[ÚU]NICA)\b', re.I),
    'subsecao': re.compile(r'^SUBSE[ÇC][ÃA]O\s+([IVXLC]+|[ÚU]NICA)\b', re.I),
}

nodes = {}          # id -> estrutura
order = []
root = {'id': 'raiz', 'tipo': 'raiz', 'rotulo': 'Plano Diretor (Rev.05)', 'nome': '', 'pai': None}
nodes['raiz'] = root; order.append('raiz')
current = {'titulo': None, 'capitulo': None, 'secao': None, 'subsecao': None}
pending = None      # (nivel, rotulo) à espera do nome

artigos = []
art = None
ctx = None          # dispositivo atual que recebe incisos (caput ou §)
cnt_inc = cnt_ali = cnt_item = 0
glossario = []
ementa = []
in_glossary = False
art_seq = 0
avisos = []


def parent_id():
    for lv in reversed(LEVELS):
        if current[lv]:
            return current[lv]
    return 'raiz'


def open_level(nivel, rotulo):
    idx = LEVELS.index(nivel)
    for lv in LEVELS[idx:]:
        current[lv] = None
    pid = parent_id()
    nid = (pid + '/' if pid != 'raiz' else '') + slug(rotulo)
    node = {'id': nid, 'tipo': nivel, 'rotulo': rotulo, 'nome': '', 'pai': pid}
    nodes[nid] = node; order.append(nid)
    current[nivel] = nid
    return node


def close_article():
    global art
    if art:
        artigos.append(art)
    art = None


for p in d.paragraphs:
    t = ptext(p)
    if not t:
        continue
    style = p.style.name
    is_heading = style.startswith('Heading') or style == 'SEÇÃO'

    if is_heading and re.match(r'^GLOSS[ÁA]RIO', t, re.I):
        close_article(); in_glossary = True
        continue
    if in_glossary:
        m = re.match(r'^(?:O |A )?([^:]{2,140}):\s*(.+)$', t)
        if m:
            glossario.append({'termo': m.group(1).strip(), 'definicao': m.group(2).strip()})
        elif glossario:
            glossario[-1]['definicao'] += ' ' + t
        continue

    # cabeçalhos estruturais
    matched = False
    for nivel, rx in LABEL_RE.items():
        if rx.match(t) and (is_heading or len(t) < 30):
            close_article()
            rot = t.split(' ')[0].capitalize() + ' ' + t.split(' ')[1].upper()
            rot = rot.replace('Titulo', 'Título').replace('Capitulo', 'Capítulo').replace('Secao', 'Seção').replace('Subsecao', 'Subseção')
            node = open_level(nivel, rot)
            rest = rx.sub('', t).strip(' -–—')
            if rest:
                node['nome'] = rest
            else:
                pending = node
            matched = True
            break
    if matched:
        continue
    if is_heading:
        if pending is not None:
            pending['nome'] = t; pending = None
        else:
            avisos.append('Cabeçalho sem rótulo: ' + t[:80])
        continue
    pending = None

    ni = numinfo(p)
    typed_art = re.match(r'^Art\.?\s*(\d+)\s*[º°o]?\s*\.?\s*[-–]?\s*', t)
    auto_art = ni and ni['txt'].startswith('Art.')

    if typed_art or auto_art:
        close_article()
        art_seq += 1
        if typed_art:
            n = int(typed_art.group(1))
            if n != art_seq:
                avisos.append(f'Numeração digitada {n} difere da sequência {art_seq}')
            texto = t[typed_art.end():]
        else:
            n = art_seq
            texto = t
        art = {'n': n, 'id': f'art-{n}', 'rotulo': art_label(n), 'pai': parent_id(),
               'dispositivos': [{'tipo': 'caput', 'rotulo': '', 'texto': texto}]}
        ctx = 'caput'; cnt_inc = cnt_ali = cnt_item = 0
        continue

    if art is None:
        if not artigos:
            ementa.append(t)
        else:
            avisos.append('Texto fora de artigo: ' + t[:80])
        continue

    m_par = re.match(r'^(§\s*(\d+)\s*[º°o]?\.?|Par[áa]grafo [úu]nico\.?)\s*[-–]?\s*', t, re.I)
    if m_par:
        rot = ('§ ' + m_par.group(2) + ('º' if int(m_par.group(2)) < 10 else '')) if m_par.group(2) else 'Parágrafo único'
        art['dispositivos'].append({'tipo': 'paragrafo', 'rotulo': rot, 'texto': t[m_par.end():]})
        cnt_inc = cnt_ali = cnt_item = 0
        continue

    fmt = ni['fmt'] if ni else None
    m_typed_inc = re.match(r'^([IVXLC]+)\s*[-–—]\s+', t)
    m_typed_ali = re.match(r'^([a-z])\)\s+', t)
    if fmt == 'upperRoman' or m_typed_inc:
        cnt_inc += 1; cnt_ali = cnt_item = 0
        texto = t[m_typed_inc.end():] if m_typed_inc else t
        art['dispositivos'].append({'tipo': 'inciso', 'rotulo': roman(cnt_inc), 'texto': texto})
    elif fmt == 'lowerLetter' or m_typed_ali:
        cnt_ali += 1; cnt_item = 0
        texto = t[m_typed_ali.end():] if m_typed_ali else t
        art['dispositivos'].append({'tipo': 'alinea', 'rotulo': chr(96 + cnt_ali) + ')', 'texto': texto})
    elif fmt == 'decimal':
        cnt_item += 1
        art['dispositivos'].append({'tipo': 'item', 'rotulo': f'{cnt_item}.', 'texto': t})
    else:
        art['dispositivos'].append({'tipo': 'texto', 'rotulo': '', 'texto': t})

close_article()

# ---------- caminho (breadcrumb) de cada artigo ----------
for a in artigos:
    cam = []
    pid = a['pai']
    while pid and pid != 'raiz':
        nd = nodes[pid]
        cam.append({'id': nd['id'], 'rotulo': nd['rotulo'], 'nome': nd['nome'], 'tipo': nd['tipo']})
        pid = nd['pai']
    a['caminho'] = list(reversed(cam))

# ---------- texto corrido, remissões, classificação ----------
EXTERNO = re.compile(r'^\s*(?:,\s*)?(?:§[^,;]*?,?\s*)?(?:(?:inciso|incisos|al[íi]nea)[^,;]*?,?\s*)?(?:[IVX]+(?:\s*e\s*[IVX]+)?,?\s*)?(da|do|dos|das)\s+(Lei|Decreto|Constitui|Estatuto|C[óo]digo|Resolu|Medida|Portaria|Instru|Norma|ABNT|NBR|Conven|Emenda|LC)', re.I)
REF_RE = re.compile(r'\b(?:arts?\.|artigos?)\s+((?:\d+(?:\s*[º°])?(?:\s*,\s*|\s+(?:e|a|ou|até)\s+)?)+)', re.I)
LEI_RE = re.compile(r'\b((?:Decreto-Lei|Lei)(?: Complementar| Federal| Estadual| Municipal| Org[âa]nica)?(?: Federal| Municipal| Estadual)?\s+(?:n[º°o.]*\s*)?\d[\d.]*(?:/\d+)?(?:,\s*de\s+\d{1,2}(?:º)?\s+de\s+\w+\s+de\s+\d{4})?)', re.I)
ANEXO_RE = re.compile(r'\bAnexo\s+(\d+[A-Z]?)(?:\s*[–—-]\s*([^,.;]+?))?(?=\s+deste|\s+desta|\s*,|\s*;|\s*\.|\s+e\s+Anexo|$)')
NIV = r'(T[íi]tulo|Cap[íi]tulo|Subse[çc][ãa]o|Se[çc][ãa]o)\s+([IVXLC]+|[ÚU]nic[oa])\b'
ESTR_RE = re.compile(NIV + r'(?:(?:\s*,\s*(?:d[oa]\s+)?|\s+d[oa]\s+)' + NIV + r')?(?:(?:\s*,\s*(?:d[oa]\s+)?|\s+d[oa]\s+)' + NIV + r')?', re.I)
REL_RE = re.compile(r'\b(?:neste|deste|nesta|desta)\s+(Cap[íi]tulo|Se[çc][ãa]o|Subse[çc][ãa]o|T[íi]tulo)\b', re.I)

COMANDOS = {
    'obrigacao': r'\b(dever[áã]o|deverá|devem|deve|fica[m]? obrigad[oa]s?|é obrigat[óo]ri[oa]|s[ãa]o obrigat[óo]ri[oa]s|ser[áã]o? obrigat[óo]ri[oa]s?|obriga-se|obrigam-se)\b',
    'competencia': r'\b(compete|competir[áã]o?|competem|cabe|caber[áã]o?|incumbe|é responsável|ser[áã]o? responsáve(?:l|is))\b',
    'permissao': r'(?<!não )\b(poder[áã]o|poderá|podem|pode|fica[m]? autorizad[oa]s?|é facultad[oa]|fica facultad[oa]|faculta-se|é permitid[oa]|ser[áã]o? permitid[oa]s?|admite-se|admitir-se-[áa])\b',
    'proibicao': r'\b(é vedad[oa]|fica[m]? vedad[oa]s?|vedad[oa]s?|vedação|é proibid[oa]|fica[m]? proibid[oa]s?|proíbe-se|não poder[áã]o|não poderá|não ser[áã]o? permitid[oa]s?|não se admite|não será admitid[oa])\b',
    'regulamentacao': r'\b(lei específica|leis específicas|lei municipal específica|regulamenta[çc][ãa]o|regulamentad[oa]s?|regulamento|ato do Poder Executivo|decreto municipal|ato normativo|legislação específica|legislação complementar)\b',
    'prazo': r'(\bno prazo (?:máximo )?de\b|\bprazo de \d+|\bem até \d+|\baté (?:o ano de )?\d{4}\b|\banualmente\b|\ba cada \d+\s*(?:\(\w+\)\s*)?(?:anos|meses)\b|\b\d+\s*\([\w\s]+\)\s*(?:dias|meses|anos)\b)',
}
COMANDOS_RE = {k: re.compile(v, re.I) for k, v in COMANDOS.items()}

all_n = {a['n'] for a in artigos}
TIPO_DE = {'t': 'titulo', 'c': 'capitulo', 's': 'secao', 'u': 'subsecao'}


def tipo_nivel(w):
    w = unicodedata.normalize('NFKD', w).encode('ascii', 'ignore').decode().lower()
    return {'titulo': 'titulo', 'capitulo': 'capitulo', 'secao': 'secao', 'subsecao': 'subsecao'}[w]


def rot_nivel(tipo, num):
    nome = {'titulo': 'Título', 'capitulo': 'Capítulo', 'secao': 'Seção', 'subsecao': 'Subseção'}[tipo]
    num = num.upper() if not num.lower().startswith(('ú', 'u')) else num.upper()
    return nome + ' ' + num


def resolve_estrutura(a, partes):
    """partes: lista de (tipo, numero) em qualquer ordem; completa com o caminho do artigo."""
    dado = {t: n for t, n in partes}
    cam = {c['tipo']: c for c in a['caminho']}
    alvo_nivel = max(dado, key=LEVELS.index)
    path = []
    for lv in LEVELS[:LEVELS.index(alvo_nivel) + 1]:
        if lv in dado:
            path.append(slug(rot_nivel(lv, dado[lv])))
        elif lv in cam and all(LEVELS.index(lv) < LEVELS.index(x) for x in dado):
            path.append(cam[lv]['id'].split('/')[-1])
        elif lv in ('secao', 'subsecao'):
            continue
        else:
            return None
    nid = '/'.join(path)
    if nid in nodes:
        return nid
    # seção pode estar direto no título (sem capítulo) — procura por sufixo
    cands = [k for k in nodes if k.endswith('/' + path[-1]) and all(p in k.split('/') for p in path)]
    return cands[0] if len(cands) == 1 else None


def expand_nums(s):
    out = set(int(x) for x in re.findall(r'\d+', s))
    for x, y in re.findall(r'(\d+)(?:\s*[º°])?\s+(?:a|até)\s+(\d+)', s):
        x, y = int(x), int(y)
        if 0 < y - x < 60:
            out.update(range(x, y + 1))
    return out


remissoes = []
leis = Counter()
anexos = {}
for a in artigos:
    partes = []
    for disp in a['dispositivos']:
        r = disp['rotulo']
        if disp['tipo'] == 'inciso':
            r = r + ' –'
        partes.append((r + ' ' if r else '') + disp['texto'])
    a['texto'] = '\n'.join(partes)
    a['caput'] = a['dispositivos'][0]['texto']
    full = a['texto']

    refs = set()
    for m in REF_RE.finditer(full):
        if EXTERNO.match(full[m.end():m.end() + 90]):
            continue
        for n in expand_nums(m.group(1)):
            if n in all_n and n != a['n']:
                refs.add(n)
    a['remete'] = sorted(refs)

    est = set()
    for m in ESTR_RE.finditer(full):
        if EXTERNO.match(full[m.end():m.end() + 60]):
            continue
        g = m.groups()
        partes_m = [(tipo_nivel(g[i]), g[i + 1]) for i in (0, 2, 4) if g[i]]
        # "Seção III deste Capítulo"
        nid = resolve_estrutura(a, partes_m)
        if nid:
            est.add(nid)
    a['remete_estrutura'] = sorted(est)

    ax = {}
    for m in ANEXO_RE.finditer(full):
        k = m.group(1).upper()
        nome = (m.group(2) or '').strip(' –-')
        ax[k] = nome
        if k not in anexos or (nome and not anexos[k]):
            anexos[k] = nome
    a['anexos'] = sorted(ax, key=lambda k: (int(re.match(r'\d+', k).group()), k))

    a['leis'] = sorted({re.sub(r'\s+', ' ', m.group(1)).strip(' ,') for m in LEI_RE.finditer(full)})
    for l in a['leis']:
        leis[l] += 1
    a['comandos'] = {k: len(rx.findall(full)) for k, rx in COMANDOS_RE.items()}
    for n in refs:
        remissoes.append({'de': a['n'], 'para': n, 'tipo': 'artigo'})
    for nid in est:
        remissoes.append({'de': a['n'], 'para': nid, 'tipo': 'estrutura'})
    for k in a['anexos']:
        remissoes.append({'de': a['n'], 'para': 'anexo-' + k, 'tipo': 'anexo'})

citado_por = defaultdict(list)
for r in remissoes:
    if r['tipo'] == 'artigo':
        citado_por[r['para']].append(r['de'])
for a in artigos:
    a['citado_por'] = sorted(citado_por.get(a['n'], []))

estrutura = [nodes[i] for i in order]
for nd in estrutura:
    nd['n_artigos'] = 0
for a in artigos:
    pid = a['pai']
    while pid:
        nodes[pid]['n_artigos'] += 1
        pid = nodes[pid]['pai']

dados = {
    'documento': {
        'titulo': 'Plano Diretor de Curitiba — Projeto de Lei Rev.05 r1 (atualizada)',
        'arquivo': '14c. Plano Diretor - Projeto Rev.05_r1 ATUALIZADA.docx',
        'ementa': ' '.join(ementa),
        'gerado_em': __import__('datetime').date.today().isoformat(),
    },
    'estrutura': estrutura,
    'artigos': artigos,
    'remissoes': remissoes,
    'glossario': glossario,
    'anexos': [{'id': 'anexo-' + k, 'rotulo': 'Anexo ' + k, 'nome': v} for k, v in sorted(anexos.items(), key=lambda kv: (int(re.match(r'\d+', kv[0]).group()), kv[0]))],
    'leis_citadas': [{'lei': k, 'n': v} for k, v in leis.most_common()],
    'avisos': avisos,
}

import os, copy
os.makedirs(OUT, exist_ok=True)
textos = {a['n']: a['texto'] for a in artigos}
for a in dados['artigos']:
    a.pop('texto', None); a.pop('caput', None)
with open(os.path.join(OUT, 'minuta_rev05.json'), 'w', encoding='utf-8') as f:
    json.dump(dados, f, ensure_ascii=False)
with open(os.path.join(OUT, 'minuta_rev05.js'), 'w', encoding='utf-8') as f:
    f.write('window.MINUTA = ')
    json.dump(dados, f, ensure_ascii=False)
    f.write(';\n')
with open(os.path.join(OUT, 'minuta_rev05_chunks.jsonl'), 'w', encoding='utf-8') as f:
    for a in artigos:
        cam = ' > '.join((c['rotulo'] + (' — ' + c['nome'] if c['nome'] else '')) for c in a['caminho'])
        f.write(json.dumps({
            'id': f"rev05-{a['id']}",
            'texto': f"{cam}\n{a['rotulo']} {textos[a['n']]}",
            'metadata': {'versao': 'Rev.05 r1', 'artigo': a['n'], 'rotulo': a['rotulo'],
                         'titulo': a['caminho'][0]['rotulo'] if a['caminho'] else None,
                         'caminho': cam, 'remete': a['remete'], 'citado_por': a['citado_por']}
        }, ensure_ascii=False) + '\n')

print('artigos', len(artigos), 'nós estruturais', len(estrutura), 'remissões', len(remissoes),
      'glossário', len(glossario), 'avisos', len(avisos))
print(Counter(n['tipo'] for n in estrutura))
for w in avisos[:20]:
    print(' !', w)
