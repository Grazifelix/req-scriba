"""Schemas de áudios, transcrições, documentos e busca."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from app.schemas.common import InputModel, LongText, OutputModel


class AudioOut(OutputModel):
    id: int
    projeto_id: int
    nome_arquivo: str
    tamanho_bytes: int
    duracao_segundos: Optional[float]
    duracao_formatada: Optional[str]
    status: str = Field(description="pendente | processando | concluido | erro")
    status_rotulo: str = Field(examples=["Transcrição em andamento"])
    mensagem_erro: Optional[str]
    total_segmentos: int
    url_arquivo: str
    pode_reprocessar: bool


class AudioList(OutputModel):
    itens: list[AudioOut]
    total: int


class SegmentOut(OutputModel):
    id: int
    posicao: int
    texto: str = Field(description="Texto revisado (usado nas extrações)")
    texto_original: str = Field(description="Saída original do mecanismo de transcrição")
    editado: bool
    inicio_segundos: float
    fim_segundos: float
    inicio_formatado: str
    fim_formatado: str
    url_momento: str = Field(description="URL do áudio posicionada no início do segmento (#t=)")


class TranscriptionOut(OutputModel):
    audio_id: int
    nome_arquivo: str
    status: str
    status_rotulo: str
    duracao_segundos: Optional[float]
    texto_completo: str
    segmentos: list[SegmentOut]
    total_segmentos: int


class SegmentSearchOut(OutputModel):
    termo: str
    itens: list[SegmentOut]
    total: int


class SegmentUpdate(InputModel):
    texto: LongText = Field(description="Texto corrigido do segmento")


class DocumentOut(OutputModel):
    id: int
    projeto_id: int
    nome_arquivo: str
    tamanho_bytes: int
    status: str
    status_rotulo: str
    mensagem_erro: Optional[str]
    total_paginas: Optional[int]
    total_caracteres: int
    total_trechos: int
    pode_reprocessar: bool


class DocumentList(OutputModel):
    itens: list[DocumentOut]
    total: int


class ChunkOut(OutputModel):
    trecho_id: int
    tipo_fonte: str
    fonte_id: int
    nome_arquivo: str
    posicao: int
    texto: str
    localizacao: dict[str, Any]
    relevancia: Optional[float] = None


class DocumentContentOut(OutputModel):
    documento_id: int
    nome_arquivo: str
    status: str
    status_rotulo: str
    mensagem_erro: Optional[str]
    texto_extraido: str
    trechos: list[ChunkOut]
    total_trechos: int


class SearchOut(OutputModel):
    termo: str
    itens: list[ChunkOut]
    total: int
