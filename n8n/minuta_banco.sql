-- Registro e cache das perguntas da aba "Pergunte usando I.A." (Consulta à Minuta).
-- Fica no mesmo banco do MultipleBrains (Supabase "orquestraia"), em tabelas próprias com prefixo minuta_.
-- Só cria objetos novos: não altera nada das tabelas do painel de especialistas.

CREATE TABLE IF NOT EXISTS minuta_consulta (
  id              BIGSERIAL PRIMARY KEY,
  criado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),
  pergunta        TEXT        NOT NULL,
  pergunta_norm   TEXT        NOT NULL,                 -- sem acento, minúsculas, espaços únicos
  versao          TEXT        NOT NULL,                 -- versão da minuta (ex.: 'Rev.05 r1')
  chave           TEXT        NOT NULL,                 -- md5(versao | pergunta_norm), usada no cache
  tipo            TEXT        NOT NULL,                 -- definicao | artigo | localizar | alteracao | tema
  rota            TEXT        NOT NULL CHECK (rota IN ('programa', 'ia', 'cache')),
  resposta        JSONB       NOT NULL,                 -- o mesmo JSON devolvido ao site
  valida          BOOLEAN     NOT NULL DEFAULT TRUE,    -- false = resposta cortada/vazia (não entra no cache)
  modelo          TEXT,
  tokens_entrada  INTEGER     NOT NULL DEFAULT 0,
  tokens_saida    INTEGER     NOT NULL DEFAULT 0,
  custo_usd       NUMERIC(10, 6) NOT NULL DEFAULT 0,
  duracao_ms      INTEGER,
  origem_cache_id BIGINT REFERENCES minuta_consulta(id)  -- quando rota = 'cache', de qual consulta veio
);
CREATE INDEX IF NOT EXISTS ix_minuta_consulta_chave ON minuta_consulta (chave, criado_em DESC);
CREATE INDEX IF NOT EXISTS ix_minuta_consulta_data  ON minuta_consulta (criado_em);

-- Avaliação opcional da resposta (mesma ideia da tabela avaliacao do MultipleBrains).
CREATE TABLE IF NOT EXISTS minuta_avaliacao (
  id          BIGSERIAL PRIMARY KEY,
  consulta_id BIGINT NOT NULL REFERENCES minuta_consulta(id) ON DELETE CASCADE,
  nota        SMALLINT NOT NULL CHECK (nota BETWEEN 1 AND 5),
  comentario  TEXT,
  criado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cache: devolve a resposta mais recente, válida e gerada por IA para a mesma pergunta na mesma versão.
-- Uma avaliação ruim (nota <= 2) tira a resposta do cache.
CREATE OR REPLACE FUNCTION fn_minuta_cache_buscar(p_pergunta_norm TEXT, p_versao TEXT)
RETURNS JSONB LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object('id', c.id, 'criado_em', c.criado_em, 'resposta', c.resposta, 'custo_usd', c.custo_usd)
  FROM minuta_consulta c
  WHERE c.chave = md5(p_versao || '|' || p_pergunta_norm)
    AND c.rota = 'ia' AND c.valida
    AND NOT EXISTS (SELECT 1 FROM minuta_avaliacao a WHERE a.consulta_id = c.id AND a.nota <= 2)
  ORDER BY c.criado_em DESC
  LIMIT 1
$$;

-- Registro: recebe um único JSON (um parâmetro só, sem problema de vírgulas no n8n) e devolve o id.
CREATE OR REPLACE FUNCTION fn_minuta_registrar(p JSONB)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
  INSERT INTO minuta_consulta (pergunta, pergunta_norm, versao, chave, tipo, rota, resposta, valida, modelo,
                               tokens_entrada, tokens_saida, custo_usd, duracao_ms, origem_cache_id)
  VALUES (p->>'pergunta', p->>'pergunta_norm', p->>'versao', md5((p->>'versao') || '|' || (p->>'pergunta_norm')),
          p->>'tipo', p->>'rota', COALESCE(p->'resposta', '{}'::jsonb), COALESCE((p->>'valida')::boolean, TRUE),
          p->>'modelo', COALESCE((p->>'tokens_entrada')::int, 0), COALESCE((p->>'tokens_saida')::int, 0),
          COALESCE((p->>'custo_usd')::numeric, 0), (p->>'duracao_ms')::int, (p->>'origem_cache_id')::bigint)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- Acompanhamento de custo por dia e por rota.
CREATE OR REPLACE VIEW vw_minuta_custo_diario AS
SELECT date_trunc('day', criado_em AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
       count(*)                                   AS perguntas,
       count(*) FILTER (WHERE rota = 'programa')  AS sem_ia,
       count(*) FILTER (WHERE rota = 'cache')     AS do_cache,
       count(*) FILTER (WHERE rota = 'ia')        AS com_ia,
       round(sum(custo_usd), 4)                   AS custo_usd,
       round(avg(duracao_ms) / 1000.0, 1)         AS segundos_medios
FROM minuta_consulta
GROUP BY 1
ORDER BY 1 DESC;
