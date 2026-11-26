"""Milestone 3 (US-13, US-14, US-15, US-18): novas fontes em projetos existentes, recuperação de histórias
relacionadas, ANALISE_IMPACTO_FONTES com as cinco categorias, propostas pendentes e decisão humana."""

import re

import pytest

from app.core.errors import ConflictError
from app.domain.enums import StoryStatus
from app.services.generation import run_generation, start_generation
from app.services.stories import StoryService
from app.services.updates import UpdateService, run_update_analysis, start_analysis
from tests.conftest import ensure_approved_analysis, upload_document
from tests.fakes import default_impact_sources, labeled_texts

NEW_DOC = (
    "Ata da segunda entrevista: o boletim será disponibilizado em PDF para download.\n\n"
    "Os responsáveis querem receber notificações por e-mail quando houver faltas."
)
STAGES = ["preparacao", "recuperacao", "analise_impacto", "consolidacao", "persistencia"]


@pytest.fixture
def approved_story(container, project_id, audio_id):
    ensure_approved_analysis(container, project_id)
    with container.uow() as uow:
        job = start_generation(uow, project_id, [audio_id], [], "ana")
    run_generation(container, job["id"])
    with container.uow() as uow:
        story_id = uow.jobs.get(job["id"])["resultado"]["historias_criadas"][0]
        StoryService(uow).set_status(story_id, StoryStatus.APPROVED)
    container.llm.calls.clear()
    return story_id


def analyze(container, project_id, document_ids, reprocess=False, audio_ids=()):
    with container.uow() as uow:
        job = start_analysis(uow, project_id, list(audio_ids), document_ids, "ana", reprocess)
    run_update_analysis(container, job["id"])
    with container.uow() as uow:
        return uow.jobs.get(job["id"])


def new_doc(container, project_id, text=NEW_DOC, name="ata2.txt"):
    return upload_document(container, project_id, text, name)


def suggestions(container, project_id, story_id="any"):
    with container.uow() as uow:
        items = [UpdateService(uow).get(s["id"]) for s in UpdateService(uow).list(project_id)["itens"]]
    return items if story_id == "any" else [s for s in items if s["historia_id"] == story_id]


def story(container, story_id):
    with container.uow() as uow:
        return StoryService(uow).detail(story_id)


def single_impact(category, proposal=None, conflicts=()):
    """Handler que devolve um impacto da categoria para a história do boletim, citando o trecho do PDF."""
    def handler(prompt):
        new = labeled_texts(prompt, "fontes_novas")
        pdf = next(l for l, t in new.items() if "PDF" in t)
        ref = re.search(r"^\[(H\d+)\] US-\d+: Como coordenador", prompt, flags=re.M).group(1)
        return {"impactos_identificados": [{
            "categoria": category, "historia_ref": ref, "trechos_novos": [pdf], "justificativa": f"{category}.",
            "evidencias_relacionadas": [{"trecho_id": pdf, "afirmacoes_sustentadas": ["funcionalidade"]}],
            "alteracao_proposta": proposal, "criterios_afetados": []}],
            "novas_historias_propostas": [], "historias_nao_afetadas": [],
            "conflitos_identificados": [{**c, "historia_ref": ref} for c in conflicts]}
    return handler


PDF_PROPOSAL = {"titulo": "Emitir boletim em PDF", "perfil": "coordenador pedagógico",
                "funcionalidade": "emitir o boletim da turma em PDF", "beneficio": "acompanhar o desempenho dos alunos",
                "componentes_afetados": ["funcionalidade"]}
SAME_PROPOSAL = {**PDF_PROPOSAL, "funcionalidade": "emitir o boletim da turma", "titulo": "Emitir boletim"}


# ---------- 3.1 só as fontes novas; nada é aplicado ----------

def test_analysis_only_proposes_and_never_touches_stories(container, project_id, approved_story):
    before = story(container, approved_story)
    doc = new_doc(container, project_id)
    job = analyze(container, project_id, [doc])  # US-13 CA01
    result = job["resultado"]
    assert job["status"] == "concluido" and [s["etapa"] for s in result["etapas"]] == STAGES
    assert result["por_categoria"] == {"confirmacao": 0, "complementacao": 0, "modificacao": 1, "contradicao": 0,
                                       "nova_necessidade": 1}
    assert result["fontes_analisadas"] == [f"documento:{doc}"] and result["trechos_novos"] == 1
    items = suggestions(container, project_id)
    assert {s["status"] for s in items} == {"pendente"}  # US-14 CA01
    after = story(container, approved_story)
    assert after["historia"] == before["historia"] and after["versao_atual"] == 1  # US-14 CA04
    assert after["fontes_documentos"] == [] and after["atualizacoes_pendentes"] == 1


def test_already_processed_sources_are_skipped_unless_reprocessing(container, project_id, approved_story, audio_id):
    doc = new_doc(container, project_id)
    analyze(container, project_id, [doc])
    container.llm.calls.clear()
    job = analyze(container, project_id, [doc], audio_ids=[audio_id])  # áudio já usado na geração
    assert job["status"] == "concluido" and job["resultado"]["total"] == 0
    assert sorted(job["resultado"]["fontes_ignoradas"]) == [f"audio:{audio_id}", f"documento:{doc}"]
    assert "já foram processadas" in job["resultado"]["mensagem"]
    assert not container.llm.prompts_for("ANALISE_IMPACTO_FONTES")
    job = analyze(container, project_id, [doc], reprocess=True)
    assert job["resultado"]["fontes_analisadas"] == [f"documento:{doc}"] and job["resultado"]["total"] == 2


# ---------- 3.2 recuperação de histórias relacionadas ----------

def test_prompt_carries_new_excerpts_candidates_evidence_and_criteria(container, project_id, approved_story):
    analyze(container, project_id, [new_doc(container, project_id)])
    [prompt] = container.llm.prompts_for("ANALISE_IMPACTO_FONTES")
    assert container.llm.schemas["ANALISE_IMPACTO_FONTES"]["title"] == "ANALISE_IMPACTO_FONTES"
    assert "<fontes_novas>\n[F1] (documento ata2.txt" in prompt
    assert re.search(r"^\[E1\] \(evidência atual de US-001 · áudio reuniao\.wav", prompt, flags=re.M)
    assert re.search(r"^\[H\d+\] US-001: Como coordenador pedagógico", prompt, flags=re.M)
    assert re.search(r"^- \[H\d+-CA1\] Dado ", prompt, flags=re.M)
    assert "Trechos novos possivelmente relacionados: F1" in prompt
    assert "ANALISE DO PROJETO:" in prompt and "Atômica" in prompt and "### TAREFA: ANALISE_IMPACTO_FONTES" in prompt


def test_retrieval_combines_functionality_terms_and_ranking(container, project_id, approved_story):
    doc = new_doc(container, project_id)
    with container.uow() as uow:
        [chunk] = uow.chunks.list_by_source("documento", doc)
        analysis = uow.analyses.latest_reviewed(project_id)
        items = uow.analysis_items.list_by_analysis(analysis["id"])
        fn = next(i for i in items if i["tipo"] == "funcionalidade")
        uow.analysis_items.update(fn["id"], {"codigo": "FN1", "evidencias": [{"trecho_id": chunk["id"]}],
                                             "historia_ids": [approved_story]})
        uow.analysis_items.insert({**{k: fn[k] for k in ("analise_id", "origem", "editado", "posicao", "criado_em",
                                                         "atualizado_em")},
                                   "tipo": "termo", "codigo": None, "texto": "boletim", "complemento": "relatório",
                                   "referencias": [], "evidencias": [], "historia_ids": []})
    container.settings.impact_top_stories = 0  # sem o sinal semântico: os outros dois bastam
    analyze(container, project_id, [doc])
    [modification] = [s for s in suggestions(container, project_id, approved_story) if s["tipo"] == "modificacao"]
    signals = {(s["sinal"], s["detalhe"]) for s in modification["detalhes"]["sinais_recuperacao"]}
    assert signals == {("funcionalidade", "FN1"), ("termo", "boletim")}


def test_story_without_any_signal_is_not_sent_to_the_model(container, project_id, approved_story):
    container.settings.impact_top_stories = 0
    job = analyze(container, project_id, [new_doc(container, project_id)])
    [prompt] = container.llm.prompts_for("ANALISE_IMPACTO_FONTES")
    assert "(nenhuma história existente relacionada)" in prompt
    assert job["resultado"]["historias_candidatas"] == 0
    assert job["resultado"]["por_categoria"]["nova_necessidade"] == 1  # trechos sem candidata ainda geram novas


def test_candidate_limit_per_call(container, project_id, approved_story):
    container.settings.impact_max_stories = 1
    job = analyze(container, project_id, [new_doc(container, project_id)])
    assert job["resultado"]["historias_analisadas"] == 1
    assert len(job["resultado"]["historias_fora_do_limite"]) == 1


# ---------- 3.3 / 3.5 as cinco categorias e o conteúdo das propostas ----------

def test_modification_proposal_contents(container, project_id, approved_story):
    analyze(container, project_id, [new_doc(container, project_id)])
    [proposal] = suggestions(container, project_id, approved_story)
    assert proposal["tipo"] == "modificacao" and proposal["tipo_rotulo"].startswith("Modificação")
    assert proposal["versao_base"] == 1 and proposal["versao_atual_historia"] == 1
    assert proposal["conteudo_atual"]["funcionalidade"] == "emitir o boletim da turma"
    assert proposal["conteudo_proposto"]["funcionalidade"] == "emitir o boletim da turma em PDF"
    assert proposal["comparacao"]["campos_alterados"] == ["titulo", "funcionalidade", "historia"]
    details = proposal["detalhes"]
    assert details["componentes_afetados"] == ["funcionalidade"] and details["categoria_original"] is None
    actions = [(c["codigo"], c["acao_recomendada"], c["origem"]) for c in details["criterios_afetados"]]
    assert actions == [("CA01", "REVISAR", "llm"), ("CA02", "REVISAR", "codigo")]  # não valem automaticamente
    [evidence] = proposal["fontes_documentos"]
    assert "PDF" in evidence["trecho"] and evidence["afirmacoes_sustentadas"] == ["funcionalidade"]


def test_confirmation_is_a_pending_proposal_and_approval_only_links_evidence(container, project_id, approved_story):
    container.llm.handlers["ANALISE_IMPACTO_FONTES"] = single_impact("CONFIRMACAO", PDF_PROPOSAL)
    analyze(container, project_id, [new_doc(container, project_id)])
    [confirmation] = suggestions(container, project_id, approved_story)
    assert confirmation["tipo"] == "confirmacao" and confirmation["status"] == "pendente"
    assert confirmation["conteudo_proposto"] == confirmation["conteudo_atual"]  # proposta do modelo ignorada
    assert story(container, approved_story)["fontes_documentos"] == []  # nada anexado antes da aprovação
    with container.uow() as uow:
        decided = UpdateService(uow).approve(confirmation["id"], "ana")
    after = story(container, approved_story)
    assert decided["status"] == "aprovada" and decided["versao_aplicada"] is None
    assert after["versao_atual"] == 1 and after["fontes_documentos"][0]["origem"] == "atualizacao"


@pytest.mark.parametrize("category,proposal,expected,original", [
    ("COMPLEMENTACAO", SAME_PROPOSAL, "complementacao", None),
    ("COMPLEMENTACAO", PDF_PROPOSAL, "modificacao", "complementacao"),  # mudou a funcionalidade
    ("MODIFICACAO", SAME_PROPOSAL, "complementacao", "modificacao"),  # não mudou nada
    ("MODIFICACAO", PDF_PROPOSAL, "modificacao", None),
])
def test_complementation_and_modification_are_classified_by_real_change(
        container, project_id, approved_story, category, proposal, expected, original):
    container.llm.handlers["ANALISE_IMPACTO_FONTES"] = single_impact(category, proposal)
    analyze(container, project_id, [new_doc(container, project_id)])
    [item] = suggestions(container, project_id, approved_story)
    assert item["tipo"] == expected and item["detalhes"]["categoria_original"] == original
    changed = item["conteudo_proposto"]["funcionalidade"] != item["conteudo_atual"]["funcionalidade"]
    assert changed is (expected == "modificacao")


def test_complementation_approval_links_evidence_without_new_version(container, project_id, approved_story):
    container.llm.handlers["ANALISE_IMPACTO_FONTES"] = single_impact("COMPLEMENTACAO", None)
    analyze(container, project_id, [new_doc(container, project_id)])
    [item] = suggestions(container, project_id, approved_story)
    with container.uow() as uow:
        decided = UpdateService(uow).approve(item["id"], "ana")
    assert decided["versao_aplicada"] is None and story(container, approved_story)["fontes_documentos"]


def test_contradiction_never_changes_content_and_records_conflicting_sources(container, project_id, approved_story):
    container.llm.handlers["ANALISE_IMPACTO_FONTES"] = single_impact(
        "CONTRADICAO", PDF_PROPOSAL,
        conflicts=[{"trechos_conflitantes": ["F1", "E1", "E9"], "descricao": "Formato divergente.", "impacto": "alto"}])
    analyze(container, project_id, [new_doc(container, project_id)])
    [item] = suggestions(container, project_id, approved_story)
    assert item["tipo"] == "contradicao" and item["conteudo_proposto"] == item["conteudo_atual"]
    assert "em PDF" in item["detalhes"]["alternativa_proposta"]  # só referência para o analista
    [conflict] = item["detalhes"]["conflitos"]
    origins = [(t["rotulo"], t["origem"]) for t in conflict["trechos"]]
    assert origins == [("F1", "fonte_nova"), ("E1", "evidencia_atual")]  # E9 não existe: descartado
    assert conflict["trechos"][1]["evidencia_id"] and "boletim" in conflict["trechos"][1]["texto"]


def test_conflict_without_contradiction_impact_becomes_one(container, project_id, approved_story):
    def handler(prompt):
        ref = re.search(r"^\[(H\d+)\] US-001", prompt, re.M).group(1)
        return {"impactos_identificados": [], "novas_historias_propostas": [], "historias_nao_afetadas": [],
                "conflitos_identificados": [{"historia_ref": ref, "trechos_conflitantes": ["F1"], "descricao": "Diverge."}]}
    container.llm.handlers["ANALISE_IMPACTO_FONTES"] = handler
    analyze(container, project_id, [new_doc(container, project_id)])
    [item] = suggestions(container, project_id, approved_story)
    assert item["tipo"] == "contradicao" and item["detalhes"]["conflitos"][0]["descricao"] == "Diverge."


def test_new_need_has_temporary_id_and_gets_definitive_code_on_approval(container, project_id, approved_story):
    analyze(container, project_id, [new_doc(container, project_id)])
    [need] = [s for s in suggestions(container, project_id) if s["tipo"] == "nova_necessidade"]
    assert need["historia_id"] is None and need["detalhes"]["id_temporario"] == "NOVA-1"
    assert need["conteudo_proposto"]["criterios_aceitacao"] == []  # critérios só depois de aprovada
    assert "notificações" in need["fontes_documentos"][0]["trecho"]
    with container.uow() as uow:
        decided = UpdateService(uow).approve(need["id"], "ana")
        created = StoryService(uow).detail(decided["historia_criada_id"])
    assert created["codigo"] == "US-003" and created["fontes_documentos"] and created["versao_atual"] == 1


def test_new_need_duplicating_backlog_is_discarded_and_similar_one_is_flagged(container, project_id, approved_story):
    def handler(prompt):
        label = next(iter(labeled_texts(prompt, "fontes_novas")))
        cite = [{"trecho_id": label, "afirmacoes_sustentadas": ["funcionalidade"]}]
        return {"impactos_identificados": [], "historias_nao_afetadas": [], "conflitos_identificados": [],
                "novas_historias_propostas": [
                    {"perfil": "coordenador pedagógico", "funcionalidade": "emitir o boletim da turma", "fontes": cite},
                    {"perfil": "coordenador pedagógico", "funcionalidade": "enviar o boletim aos responsáveis",
                     "fontes": cite},  # parecida (0,76): sinalizada, não descartada
                    {"perfil": "diretor", "funcionalidade": "aprovar matrículas", "fontes": ["F99"]},  # sem trecho válido
                ]}
    container.llm.handlers["ANALISE_IMPACTO_FONTES"] = handler
    job = analyze(container, project_id, [new_doc(container, project_id)])
    assert job["resultado"]["novas_descartadas_por_duplicidade"] == [
        {"funcionalidade": "emitir o boletim da turma", "historia_existente": "US-001"}]
    [need] = suggestions(container, project_id)
    assert need["detalhes"]["possiveis_duplicidades"][0]["codigo"] == "US-001"
    assert job["resultado"]["descartados"] == 1


def test_impacts_without_new_evidence_or_with_unknown_story_are_discarded(container, project_id, approved_story):
    def handler(prompt):
        data = default_impact_sources(prompt)
        base = data["impactos_identificados"][0]
        data["impactos_identificados"] += [
            {**base, "trechos_novos": ["F7"], "evidencias_relacionadas": []},
            {**base, "historia_ref": "H999"},
            {**base, "categoria": "TALVEZ"},
        ]
        return data
    container.llm.handlers["ANALISE_IMPACTO_FONTES"] = handler
    job = analyze(container, project_id, [new_doc(container, project_id)])
    assert job["resultado"]["descartados"] == 3 and job["resultado"]["por_categoria"]["modificacao"] == 1


# ---------- decisão humana ----------

def test_approve_modification_creates_version_with_new_evidence(container, project_id, approved_story):
    analyze(container, project_id, [new_doc(container, project_id)])
    [proposal] = suggestions(container, project_id, approved_story)
    with container.uow() as uow:
        decided = UpdateService(uow).approve(proposal["id"], "ana")
    after = story(container, approved_story)
    assert decided["versao_aplicada"] == 2 and after["funcionalidade"] == "emitir o boletim da turma em PDF"
    assert after["versoes"][-1]["origem_alteracao"] == "sugestao_ia" and after["status"] == "aprovada"
    with container.uow() as uow:
        trace = StoryService(uow).traceability(approved_story)
    assert len(trace["versoes"][-1]["novas_evidencia_ids"]) == 1  # US-18
    with container.uow() as uow, pytest.raises(ConflictError):
        UpdateService(uow).approve(proposal["id"], "ana")


def test_edit_then_approve_and_reject(container, project_id, approved_story):
    analyze(container, project_id, [new_doc(container, project_id)])
    proposal, need = sorted(suggestions(container, project_id), key=lambda s: s["historia_id"] is None)
    with container.uow() as uow:
        UpdateService(uow).approve(proposal["id"], "bia", {"beneficio": "acompanhar as notas"})
        rejected = UpdateService(uow).reject(need["id"], "bia")
    after = story(container, approved_story)
    assert after["beneficio"] == "acompanhar as notas" and after["versoes"][-1]["origem_alteracao"] == "sugestao_ia_editada"
    assert rejected["status"] == "rejeitada" and rejected["historia_criada_id"] is None


def test_llm_failure_marks_job_error_with_stages(container, project_id, approved_story):
    container.llm.fail = True
    job = analyze(container, project_id, [new_doc(container, project_id)])
    assert job["status"] == "erro" and "falharam" in job["mensagem_erro"]
    assert {s["etapa"]: s["status"] for s in job["resultado"]["etapas"]}["analise_impacto"] == "erro"
    assert suggestions(container, project_id) == []
