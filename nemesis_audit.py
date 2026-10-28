"""Relatórios de auditoria sem exposição de URLs ou credenciais."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any


ARQUIVO_AUDITORIA_JSON = "relatorio_auditoria.json"
ARQUIVO_AUDITORIA_TXT = "relatorio_auditoria.txt"
ARQUIVO_AUDITORIA_HASHES = "relatorio_auditoria.sha256.txt"


def _formatar_bytes_resumo(valor: int) -> str:
    unidades = ("bytes", "KB", "MB", "GB", "TB")
    numero = float(max(0, int(valor)))
    indice = 0
    while numero >= 1024 and indice < len(unidades) - 1:
        numero /= 1024
        indice += 1
    return f"{numero:.2f} {unidades[indice]} ({int(valor)} bytes)"


def montar_resumo_final(
    estado: dict[str, Any],
    concluiu: bool,
    erro_fatal: str | None = None,
) -> str:
    """Monta a mensagem final única, sem expor URLs ou credenciais."""
    remoto = estado.get("remote_inventory_summary", {})
    integridade = estado.get("integrity_summary", {})
    por_produto_remoto = remoto.get("por_produto", {}) or {}
    por_conta_remoto = remoto.get("por_conta", {}) or {}
    por_produto_final = integridade.get("por_produto", {}) or {}
    etapas = estado.get("link_status", {}) or {}
    pendentes = sum(valor != "concluido" for valor in etapas.values())
    avisos_origem = int(integridade.get("avisos_falha_origem") or 0)
    recuperados = sum(bool(item.get("recuperacao_isolada")) for item in (estado.get("extraction_checks") or {}).values() if isinstance(item, dict))
    erros_leitura = len((integridade.get("inventario_final") or {}).get("erros_leitura") or [])
    produtos = sorted(set(por_produto_remoto) | set(por_produto_final))

    integral = bool(
        concluiu
        and remoto.get("completo")
        and integridade.get("http_totalmente_verificado")
        and integridade.get("integridade_extracao_confirmada")
        and not avisos_origem
        and not pendentes
        and not erro_fatal
    )
    if integral:
        resultado = "CONCLUÍDO E CONFERIDO — nada faltando foi detectado."
    elif not concluiu or pendentes or erro_fatal or not integridade.get("integridade_extracao_confirmada"):
        resultado = "INCOMPLETO OU COM ERRO — não considerar a coleta integral."
    elif avisos_origem:
        resultado = "CONCLUÍDO COM FALHAS INFORMADAS PELA APPLE."
    else:
        resultado = "INCOMPLETO OU COM ERRO — não considerar a coleta integral."

    linhas = [
        "RESUMO FINAL NÊMESIS",
        resultado,
        "",
        "Origem: Apple, conforme os links HTTPS do manifesto fornecido.",
        f"Links únicos previstos: {remoto.get('arquivos', len(etapas))}",
        f"Links com tamanho obtido antes de baixar: {remoto.get('arquivos_com_tamanho', 0)}",
        f"Total remoto previsto: {_formatar_bytes_resumo(int(remoto.get('bytes_esperados') or 0))}",
        f"Total recebido: {_formatar_bytes_resumo(int(integridade.get('bytes_recebidos') or 0))}",
        f"Conteúdo declarado nos ZIPs: {_formatar_bytes_resumo(int(integridade.get('bytes_manifestados_zip') or 0))}",
        f"Conteúdo novo gravado: {_formatar_bytes_resumo(int(integridade.get('bytes_extraidos') or 0))}",
        f"Itens pendentes/com erro: {pendentes}",
        f"Avisos de falha da Apple: {avisos_origem}",
        f"ZIPs recuperados em pastas isoladas: {recuperados}",
        f"Erros de leitura no inventário final: {erros_leitura}",
    ]
    if erro_fatal:
        linhas.append(f"Erro final: {erro_fatal}")
    if pendentes:
        linhas.extend(["", "PENDÊNCIAS: consulte relatorio_auditoria.txt para identificar os pacotes e as orientações."])
    if avisos_origem:
        linhas.append("A Apple informou falhas na criação do pacote; consulte os detalhes no relatório.")
    if produtos:
        linhas.extend(["", "POR PRODUTO/NUVEM:"])
        for produto in produtos:
            previsto = por_produto_remoto.get(produto, {})
            final = por_produto_final.get(produto, {})
            nome_produto = (
                final.get("nome_produto")
                or previsto.get("nome_produto")
                or produto
            )
            linhas.append(
                f"• {nome_produto}: remoto {_formatar_bytes_resumo(int(previsto.get('bytes_esperados') or 0))}; "
                f"ZIP {_formatar_bytes_resumo(int(final.get('bytes_manifestados_zip') or 0))}; "
                f"{final.get('arquivos_manifestados_zip', 0)} arquivo(s); "
                f"pasta {final.get('pasta_produto') or nome_produto}."
            )
    if por_conta_remoto:
        linhas.extend(["", "POR CONTA DE ORIGEM:"])
        for conta, produtos_conta in sorted(por_conta_remoto.items()):
            total_conta = sum(
                int(item.get("bytes_esperados") or 0) for item in produtos_conta.values()
            )
            linhas.append(f"• {conta}: {_formatar_bytes_resumo(total_conta)} remotos.")
    raiz = estado.get("paths", {}).get("root")
    if raiz:
        linhas.extend(["", f"Relatórios e dados: {raiz}"])
    linhas.extend(["", "Clique em OK para fechar o Nêmesis."])
    return "\n".join(linhas)


def identificador_item(valor: str) -> str:
    """Identifica uma URL sem registrar seu token ou caminho."""
    return hashlib.sha256(valor.encode("utf-8", errors="replace")).hexdigest()[:12]


def calcular_sha256(caminho: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(caminho).open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(4 * 1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest().upper()


def _gravar_texto_atomico(caminho: Path, conteudo: str) -> None:
    temporario = caminho.with_name(f"{caminho.name}.tmp")
    with temporario.open("w", encoding="utf-8", newline="\n") as arquivo:
        arquivo.write(conteudo)
        arquivo.flush()
        os.fsync(arquivo.fileno())
    os.replace(temporario, caminho)


def _download_publico(chave: str, dados: dict[str, Any]) -> dict[str, Any]:
    incidentes = []
    for incidente in dados.get("incidentes", []):
        if not isinstance(incidente, dict):
            continue
        incidentes.append(
            {
                campo: incidente.get(campo)
                for campo in ("timestamp", "tentativa", "categoria", "codigo", "mensagem", "recuperado")
                if incidente.get(campo) is not None
            }
        )
    return {
        "item_id": identificador_item(chave),
        "produto": dados.get("produto"),
        "nome_produto": dados.get("nome_produto"),
        "nome_arquivo_original": dados.get("nome_arquivo_original"),
        "bytes_esperados": dados.get("bytes_esperados"),
        "bytes_esperados_pre_download": dados.get("bytes_esperados_pre_download"),
        "bytes_recebidos": dados.get("bytes_recebidos"),
        "tamanho_http_verificado": bool(dados.get("tamanho_http_verificado")),
        "sha256_arquivo_recebido": dados.get("sha256_arquivo_recebido"),
        "incidentes": incidentes,
    }


def _extracao_publica(chave: str, dados: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": identificador_item(chave),
        "produto": dados.get("produto"),
        "nome_produto": dados.get("nome_produto"),
        "pasta_produto": dados.get("nome_produto"),
        "nome_arquivo_original": dados.get("nome_arquivo_original"),
        "arquivos_extraidos": int(dados.get("extraidos") or 0),
        "bytes_extraidos": int(dados.get("bytes_extraidos") or 0),
        "arquivos_manifestados_zip": int(dados.get("arquivos_manifestados_zip") or 0),
        "bytes_manifestados_zip": int(dados.get("bytes_manifestados_zip") or 0),
        "arquivos_reutilizados_identicos": int(dados.get("arquivos_reutilizados_identicos") or 0),
        "bytes_reutilizados_identicos": int(dados.get("bytes_reutilizados_identicos") or 0),
        "bytes_zip_compactado": int(dados.get("bytes_zip_compactado") or 0),
        "sha256_manifesto_zip": dados.get("sha256_manifesto_zip"),
        "caminhos_ajustados_windows": int(dados.get("renomeados") or 0),
        "recuperacao_isolada": bool(dados.get("recuperacao_isolada")),
        "mapa": dados.get("mapa"),
        "sha256_mapa": dados.get("sha256_mapa"),
        "zip_preservado": dados.get("zip_preservado"),
        "sha256_zip": dados.get("sha256_zip"),
        "avisos_origem": dados.get("avisos_origem") or [],
    }


def _orientacoes(incidentes: list[dict[str, Any]], avisos: list[dict[str, Any]], pendencias: list[dict[str, Any]]) -> list[str]:
    codigos = {item.get("codigo") for item in incidentes}
    orientacoes = []
    if pendencias and "EXTRACTION_PATH_CONFLICT" in codigos:
        orientacoes.append(
            "Conflito de conteúdo: preserve os ZIPs e os arquivos existentes. "
            "Extraia os pacotes afetados em locais separados e preserve as versões de "
            "entradas repetidas, registrando a origem de cada uma. Não substitua arquivos."
        )
    if pendencias and "ZIP_PATH_BLOCKED" in codigos:
        orientacoes.append(
            "Caminho bloqueado: confira o motivo no incidente. Nomes incompatíveis com "
            "Windows podem exigir extração em sistema compatível; caminhos inseguros e "
            "links simbólicos exigem análise antes de qualquer extração."
        )
    if avisos or "APPLE_ICLOUD_ARCHIVE_CREATION_FAILED" in codigos:
        orientacoes.append(
            "Falha informada pela Apple: consulte o relatório da origem e solicite novo "
            "fornecimento dos itens ausentes, se necessário. Retomar o download não recria esses itens."
        )
    if pendencias and any(item.get("etapa") == "extracao_zip" for item in incidentes):
        orientacoes.append(
            "A retomada pode reutilizar os ZIPs preservados, mas os mesmos conflitos "
            "continuarão enquanto não forem resolvidos. Mantenha a execução como parcial."
        )
    return orientacoes


def construir_relatorio(estado: dict[str, Any], caminho_log: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    produtos = estado.get("products", {})
    total_manifestado = sum(len(links) for links in produtos.values() if isinstance(links, list))
    etapas = estado.get("link_status", {})
    contagem_etapas = dict(sorted(Counter(etapas.values()).items()))
    downloads_brutos = estado.get("download_checks", {})
    extracoes_brutas = estado.get("extraction_checks", {})
    inventario_remoto_bruto = estado.get("remote_inventory", {})
    inventario_remoto = sorted(
        (
            {
                "item_id": identificador_item(chave),
                "produto": dados.get("produto"),
                "nome_produto": dados.get("nome_produto"),
                "nome_arquivo_original": dados.get("nome_arquivo_original"),
                "origens": dados.get("origens") or [],
                "bytes_esperados": dados.get("bytes_esperados"),
                "metodo": dados.get("metodo"),
                "etag": dados.get("etag"),
                "last_modified": dados.get("last_modified"),
            }
            for chave, dados in inventario_remoto_bruto.items()
            if isinstance(chave, str) and isinstance(dados, dict)
        ),
        key=lambda item: item["item_id"],
    )
    downloads = sorted(
        (
            _download_publico(chave, dados)
            for chave, dados in downloads_brutos.items()
            if isinstance(chave, str) and isinstance(dados, dict)
        ),
        key=lambda item: item["item_id"],
    )
    extracoes = sorted(
        (
            _extracao_publica(chave, dados)
            for chave, dados in extracoes_brutas.items()
            if isinstance(chave, str) and isinstance(dados, dict)
        ),
        key=lambda item: item["item_id"],
    )
    avisos_origem = [
        aviso
        for extracao in extracoes
        for aviso in extracao.get("avisos_origem", [])
        if isinstance(aviso, dict)
    ]
    avisos_origem = [
        aviso
        for aviso in estado.get("principal_extraction_check", {}).get("avisos_origem", [])
        if isinstance(aviso, dict)
    ] + avisos_origem
    incidentes = [item for item in estado.get("incidentes", []) if isinstance(item, dict)]
    for download in downloads:
        for incidente in download.get("incidentes", []):
            incidentes.append({"item_id": download["item_id"], **incidente})
    for incidente in estado.get("principal_download_check", {}).get("incidentes", []):
        if isinstance(incidente, dict):
            incidentes.append({"item_id": "principal", **incidente})
    resumo_integridade = estado.get("integrity_summary", {})
    status = estado.get("status", "desconhecido")
    pendencias = []
    metadados = estado.get("product_metadata", {}) or {}
    for chave, etapa in etapas.items():
        if etapa == "concluido" or not isinstance(chave, str):
            continue
        dados = metadados.get(chave) or inventario_remoto_bruto.get(chave) or {}
        pendencias.append({
            "item_id": identificador_item(chave),
            "etapa": etapa,
            "produto": dados.get("nome_produto") or dados.get("produto"),
            "nome_arquivo_original": dados.get("nome_arquivo_original"),
        })

    if status != "concluido":
        classificacao = "EXECUCAO_INCOMPLETA"
        conclusao = "A execução ainda não terminou ou possui itens pendentes."
    elif avisos_origem:
        classificacao = "CONCLUIDO_COM_FALHAS_DECLARADAS_PELA_ORIGEM"
        conclusao = "O processamento terminou, mas a própria Apple declarou falhas ao criar o pacote de origem."
    elif not resumo_integridade.get("integridade_extracao_confirmada", True):
        classificacao = "EXTRACAO_NAO_CONFIRMADA"
        conclusao = "A execução terminou sem comprovação integral da árvore extraída."
    elif not resumo_integridade.get("http_totalmente_verificado"):
        classificacao = "CONCLUIDO_COM_RESSALVA_DE_AUDITORIA"
        conclusao = "O processamento terminou, mas nem todos os tamanhos HTTP puderam ser comprovados."
    else:
        classificacao = "CONCLUIDO_VERIFICADO"
        conclusao = "Todos os itens manifestados foram processados e tiveram os tamanhos HTTP conhecidos conferidos."

    hash_log = None
    if caminho_log and Path(caminho_log).is_file():
        hash_log = calcular_sha256(caminho_log)

    return {
        "schema_relatorio": 1,
        "gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "aplicativo": {
            "nome": "Nêmesis",
            "versao_execucao": estado.get("app_version"),
            "versao_auditor": estado.get("audit_app_version") or estado.get("app_version"),
        },
        "execucao": {
            "id": Path(estado.get("paths", {}).get("root", "")).name,
            "analista": estado.get("analyst"),
            "criada_em": estado.get("created_at"),
            "atualizada_em": estado.get("updated_at"),
            "status": status,
            "fase": estado.get("fase"),
        },
        "resultado": {"classificacao": classificacao, "conclusao": conclusao},
        "manifesto": {
            "produtos": len(produtos),
            "arquivos": total_manifestado,
            "etapas": contagem_etapas,
        },
        "integridade": resumo_integridade,
        "inventario_remoto_antes_download": {
            "resumo": estado.get("remote_inventory_summary", {}),
            "itens": inventario_remoto,
        },
        "download_principal": estado.get("principal_download_check"),
        "extracao_principal": estado.get("principal_extraction_check"),
        "downloads": downloads,
        "extracoes": extracoes,
        "avisos_de_falha_na_origem": avisos_origem,
        "incidentes": incidentes,
        "pendencias": pendencias,
        "orientacoes": _orientacoes(incidentes, avisos_origem, pendencias),
        "evidencias": {
            "log": Path(caminho_log).name if caminho_log else None,
            "sha256_log": hash_log,
        },
        "nota": (
            "O relatório comprova o que foi entregue e observado pelo Nêmesis. "
            "Ele não consegue detectar dados omitidos pela origem quando nenhum manifesto ou aviso os menciona."
        ),
    }


def _relatorio_texto(relatorio: dict[str, Any]) -> str:
    resultado = relatorio["resultado"]
    manifesto = relatorio["manifesto"]
    integridade = relatorio.get("integridade", {})
    avisos = relatorio.get("avisos_de_falha_na_origem", [])
    incidentes = relatorio.get("incidentes", [])
    linhas = [
        "RELATÓRIO DE AUDITORIA NÊMESIS",
        "=" * 72,
        f"Gerado em: {relatorio['gerado_em']}",
        f"Versão da execução: {relatorio['aplicativo'].get('versao_execucao')}",
        f"Versão do auditor: {relatorio['aplicativo'].get('versao_auditor')}",
        f"Execução: {relatorio['execucao'].get('id')}",
        f"Analista: {relatorio['execucao'].get('analista')}",
        f"Classificação: {resultado['classificacao']}",
        f"Conclusão: {resultado['conclusao']}",
        "",
        "MANIFESTO E PROCESSAMENTO",
        f"Produtos: {manifesto['produtos']}",
        f"Arquivos/links manifestados: {manifesto['arquivos']}",
        f"Etapas finais: {json.dumps(manifesto['etapas'], ensure_ascii=False, sort_keys=True)}",
        "",
        "INTEGRIDADE E TAMANHOS",
        f"Downloads registrados: {integridade.get('downloads_registrados', 0)}",
        f"Downloads com tamanho HTTP conferido: {integridade.get('downloads_http_verificados', 0)}",
        f"Downloads sem tamanho HTTP anunciado: {integridade.get('downloads_sem_tamanho_http', 0)}",
        f"Bytes previstos antes dos downloads: {integridade.get('bytes_remotos_previstos_antes_download', 0)}",
        f"Bytes HTTP esperados conhecidos: {integridade.get('bytes_http_esperados_conhecidos', 0)}",
        f"Bytes recebidos: {integridade.get('bytes_recebidos', 0)}",
        f"Bytes extraídos e conferidos: {integridade.get('bytes_extraidos', 0)}",
        f"Arquivos declarados dentro dos ZIPs: {integridade.get('arquivos_manifestados_zip', 0)}",
        f"Bytes declarados dentro dos ZIPs: {integridade.get('bytes_manifestados_zip', 0)}",
        f"Arquivos idênticos reutilizados sem substituição: {integridade.get('arquivos_reutilizados_identicos', 0)}",
        f"Bytes idênticos reutilizados sem substituição: {integridade.get('bytes_reutilizados_identicos', 0)}",
        f"Integridade da extração confirmada: {integridade.get('integridade_extracao_confirmada')}",
        f"Tamanhos pré-download confirmados: {integridade.get('tamanhos_pre_download_confirmados')}",
        "",
        "TOTAIS POR PRODUTO",
    ]
    for produto, dados in sorted((integridade.get("por_produto") or {}).items()):
        nome_produto = dados.get("nome_produto") or produto
        linhas.append(
            f"- {nome_produto}: pasta={dados.get('pasta_produto') or nome_produto}; "
            f"ZIPs={dados.get('zips', 0)}; "
            f"arquivos={dados.get('arquivos_manifestados_zip', 0)}; "
            f"bytes_no_zip={dados.get('bytes_manifestados_zip', 0)}; "
            f"bytes_gravados={dados.get('bytes_gravados', 0)}; "
            f"bytes_identicos={dados.get('bytes_reutilizados_identicos', 0)}"
        )
    recuperacoes = [item for item in relatorio.get("extracoes", []) if item.get("recuperacao_isolada")]
    linhas.extend(["", f"ZIPs RECUPERADOS EM PASTAS ISOLADAS: {len(recuperacoes)}"])
    for item in recuperacoes:
        linhas.append(
            f"- Produto: {item.get('nome_produto') or item.get('produto')} | "
            f"ZIP preservado: {item.get('zip_preservado')} | SHA-256: {item.get('sha256_zip')} | "
            f"Mapa das entradas: {item.get('mapa')} | SHA-256 do mapa: {item.get('sha256_mapa')}"
        )
    linhas.extend(["", f"FALHAS DECLARADAS PELA ORIGEM: {len(avisos)}"])
    for aviso in avisos:
        linhas.append(
            f"- [{aviso.get('codigo')}] {aviso.get('mensagem')} "
            f"Arquivo: {aviso.get('arquivo_relatorio')} Itens: {aviso.get('quantidade_itens')}"
        )
        for item in aviso.get("itens", []):
            linhas.append(f"    [{item}]")
    linhas.extend(["", f"INCIDENTES REGISTRADOS: {len(incidentes)}"])
    for incidente in incidentes:
        linhas.append(
            f"- {incidente.get('timestamp')} | {incidente.get('categoria')} | "
            f"{incidente.get('etapa')} | {incidente.get('codigo')} | {incidente.get('mensagem')}"
            f" | Pacote: {incidente.get('item') or incidente.get('item_id') or 'não informado'}"
        )
    linhas.extend(["", f"PACOTES PENDENTES: {len(relatorio.get('pendencias', []))}"])
    for item in relatorio.get("pendencias", []):
        linhas.append(
            f"- {item.get('produto') or 'Produto não informado'} | "
            f"{item.get('nome_arquivo_original') or item['item_id']} | {item.get('etapa')}"
        )
    linhas.extend(["", "ORIENTAÇÕES PARA TRATAMENTO:"])
    for orientacao in relatorio.get("orientacoes", []):
        linhas.append(f"- {orientacao}")
    linhas.extend(
        [
            "",
            "EVIDÊNCIAS",
            f"Log: {relatorio['evidencias'].get('log')}",
            f"SHA-256 do log: {relatorio['evidencias'].get('sha256_log')}",
            "",
            f"NOTA: {relatorio['nota']}",
            "",
        ]
    )
    return "\n".join(linhas)


def gerar_relatorios_auditoria(
    estado: dict[str, Any],
    caminho_log: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    raiz = Path(estado["paths"]["root"])
    raiz.mkdir(parents=True, exist_ok=True)
    relatorio = construir_relatorio(estado, caminho_log)
    caminho_json = raiz / ARQUIVO_AUDITORIA_JSON
    caminho_txt = raiz / ARQUIVO_AUDITORIA_TXT
    caminho_hashes = raiz / ARQUIVO_AUDITORIA_HASHES
    conteudo_json = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _gravar_texto_atomico(caminho_json, conteudo_json)
    _gravar_texto_atomico(caminho_txt, _relatorio_texto(relatorio))
    hashes = [
        f"{calcular_sha256(caminho_json)}  {caminho_json.name}",
        f"{calcular_sha256(caminho_txt)}  {caminho_txt.name}",
    ]
    if caminho_log and Path(caminho_log).is_file():
        hashes.append(f"{calcular_sha256(caminho_log)}  {Path(caminho_log).name}")
    _gravar_texto_atomico(caminho_hashes, "\n".join(hashes) + "\n")
    return relatorio
