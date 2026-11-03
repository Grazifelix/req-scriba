"""Contrato: as respostas dos serviços satisfazem os response_model declarados na API.

Roda sem FastAPI (apenas Pydantic) e garante que nenhuma rota falhe na serialização.
"""

from app.domain.enums import QuestionStatus
from app.schemas.projects import ProjectList, ProjectOut
from app.schemas.sources import (
    AudioList, AudioOut, DocumentContentOut, DocumentList, DocumentOut, SearchOut, SegmentOut, SegmentSearchOut,
    TranscriptionOut,
)
from app.schemas.stories import EvidenceList, StoryList, StoryOut, TraceabilityOut, VersionList, VersionOut
from app.schemas.workflow import (
    ComparisonOut, DefectList, EvaluationList, EvaluationOut, FollowupOut, JobList, JobOut, QualityOverviewOut,
    QuestionGenerateOut, QuestionList, QuestionOut, SuggestionList, SuggestionOut,
)
from app.services import retrieval
from app.services.audios import AudioService
from app.services.documents import DocumentService
from app.services.generation import run_generation, start_generation
from app.services.jobs import JobService
from app.services.projects import ProjectService
from app.services.quality import QualityService
from app.services.questions import QuestionService
from app.services.stories import StoryService
from app.services.updates import UpdateService, run_update_analysis, start_analysis
from tests.conftest import upload_document


def test_all_service_outputs_match_response_models(container, project_id, audio_id, document_id):
    with container.uow() as uow:
        projects, audios, documents = ProjectService(uow, container), AudioService(uow, container), DocumentService(uow, container)
        ProjectOut.model_validate(projects.get(project_id))
        ProjectList.model_validate(projects.list())
        AudioOut.model_validate(audios.get(audio_id))
        AudioList.model_validate(audios.list(project_id))
        TranscriptionOut.model_validate(audios.transcription(audio_id))
        SegmentSearchOut.model_validate(audios.search(audio_id, "boletim"))
        segment_id = audios.transcription(audio_id)["segmentos"][1]["id"]
        SegmentOut.model_validate(audios.update_segment(audio_id, segment_id, "O coordenador acompanha os alunos."))
        DocumentOut.model_validate(documents.get(document_id))
        DocumentList.model_validate(documents.list(project_id))
        DocumentContentOut.model_validate(documents.content(document_id))
        SearchOut.model_validate(retrieval.search(uow, container, project_id, "boletim", 5))
        job = start_generation(uow, project_id, [audio_id], [document_id], "ana")
        JobOut.model_validate(job)
    run_generation(container, job["id"])

    with container.uow() as uow:
        JobOut.model_validate(JobService(uow).get(job["id"]))
        JobList.model_validate(JobService(uow).list(project_id))
        stories = StoryService(uow)
        story_id = uow.jobs.get(job["id"])["resultado"]["historias_criadas"][0]
        StoryList.model_validate(stories.list(project_id))
        StoryOut.model_validate(stories.detail(story_id))
        EvidenceList.model_validate(stories.evidences(story_id))
        TraceabilityOut.model_validate(stories.traceability(story_id))
        VersionList.model_validate(stories.versions(story_id))
        VersionOut.model_validate(stories.version(story_id, 1))
        quality = QualityService(uow, container)
        EvaluationOut.model_validate(quality.evaluate(story_id))
        StoryOut.model_validate(stories.update(story_id, {"titulo": "Boletim"}, "ana"))
        EvaluationOut.model_validate(quality.evaluate(story_id))
        EvaluationList.model_validate(quality.history(story_id))
        ComparisonOut.model_validate(quality.compare(story_id))
        DefectList.model_validate(quality.defects(story_id))
        QualityOverviewOut.model_validate(quality.project_overview(project_id))
        questions = QuestionService(uow, container)
        generated = questions.generate(story_id)
        QuestionGenerateOut.model_validate(generated)
        question_id = generated["perguntas"][0]["id"]
        QuestionOut.model_validate(questions.edit(question_id, "Qual formato?"))
        QuestionOut.model_validate(questions.set_status(question_id, QuestionStatus.APPROVED))
        QuestionList.model_validate(questions.list(project_id))
        FollowupOut.model_validate(questions.followup(project_id, "tipo_defeito"))
        QuestionOut.model_validate(questions.answer(question_id, "PDF", None, None))

    new_doc = upload_document(container, project_id, "O boletim será em PDF.", "ata.txt")
    with container.uow() as uow:
        analysis = start_analysis(uow, project_id, [], [new_doc], "ana")
    run_update_analysis(container, analysis["id"])
    with container.uow() as uow:
        updates = UpdateService(uow)
        listed = updates.list(project_id)
        SuggestionList.model_validate(listed)
        suggestion_id = listed["itens"][0]["id"]
        SuggestionOut.model_validate(updates.get(suggestion_id))
        SuggestionOut.model_validate(updates.edit(suggestion_id, {"beneficio": "acompanhar"}))
        SuggestionOut.model_validate(updates.approve(suggestion_id, "ana"))
