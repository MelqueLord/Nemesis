"""Persistência e organização das execuções do Nêmesis."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any


ARQUIVO_ESTADO = "estado.json"
ARQUIVO_LOG = "nemesis.log"
STATUS_FINAL = "concluido"


def criar_diretorio_execucao(
    pasta_analista: str | os.PathLike[str],
    url: str,
    agora: datetime | None = None,
) -> Path:
    """Cria uma pasta exclusiva, estável e legível para uma nova execução."""
    instante = agora or datetime.now()
    identificador = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()[:8]
    raiz = Path(pasta_analista) / "execucoes"
    raiz.mkdir(parents=True, exist_ok=True)
    nome_base = f"{instante:%Y%m%d-%H%M%S}-{identificador}"
    destino = raiz / nome_base
    contador = 1
    while destino.exists():
        destino = raiz / f"{nome_base}-{contador}"
        contador += 1
    destino.mkdir(parents=True)
    return destino


def salvar_estado_atomico(caminho: str | os.PathLike[str], estado: dict[str, Any]) -> None:
    """Grava JSON sem deixar um arquivo parcialmente escrito após interrupções."""
    destino = Path(caminho)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(f"{destino.name}.tmp")
    with temporario.open("w", encoding="utf-8", newline="\n") as arquivo:
        json.dump(estado, arquivo, ensure_ascii=False, indent=2, sort_keys=True)
        arquivo.write("\n")
        arquivo.flush()
        os.fsync(arquivo.fileno())
    os.replace(temporario, destino)


def carregar_estado(caminho: str | os.PathLike[str]) -> dict[str, Any]:
    with Path(caminho).open("r", encoding="utf-8") as arquivo:
        estado = json.load(arquivo)
    if not isinstance(estado, dict) or estado.get("schema_version") != 1:
        raise ValueError("Arquivo de estado incompatível ou inválido.")
    return estado


def localizar_estado_pendente(pasta_analista: str | os.PathLike[str]) -> Path | None:
    """Retorna a execução pendente mais recente de um analista."""
    candidatos: list[Path] = []
    for caminho in (Path(pasta_analista) / "execucoes").glob(f"*/{ARQUIVO_ESTADO}"):
        try:
            if carregar_estado(caminho).get("status") != STATUS_FINAL:
                candidatos.append(caminho)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    if not candidatos:
        return None
    return max(candidatos, key=lambda item: item.stat().st_mtime_ns)


def localizar_estado_mais_recente(pasta_analista: str | os.PathLike[str]) -> Path | None:
    """Retorna a execução mais recente, inclusive quando já estiver concluída."""
    candidatos: list[Path] = []
    for caminho in (Path(pasta_analista) / "execucoes").glob(f"*/{ARQUIVO_ESTADO}"):
        try:
            carregar_estado(caminho)
            candidatos.append(caminho)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    if not candidatos:
        return None
    return max(candidatos, key=lambda item: item.stat().st_mtime_ns)
