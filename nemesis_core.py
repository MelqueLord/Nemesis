"""Funções puras e utilitários de segurança do Nêmesis."""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from urllib.parse import unquote, urlsplit


APP_VERSION = "1.6.0"

_CARACTERES_INVALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NOMES_RESERVADOS_WINDOWS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_LIMITE_COMPONENTE_ZIP = 255
_PASTA_MAPAS_EXTRACAO_LEGADA = "_nemesis_mapas"
_LIMITE_RELATORIO_ORIGEM = 5 * 1024 * 1024
_EXTENSOES_TEXTO_AUDITAVEIS = {
    ".csv",
    ".eml",
    ".htm",
    ".html",
    ".json",
    ".log",
    ".md",
    ".txt",
    ".xml",
}
_MENSAGEM_FALHA_ICLOUD = "the following files failed when creating the icloud backup data archive:"


class NomeZipIncompativel(ValueError):
    """Nome legítimo do ZIP que não pode ser representado fielmente no Windows."""


def calcular_tamanho_janela(largura_tela: int, altura_tela: int) -> tuple[int, int]:
    """Calcula uma janela que caiba inclusive em sessões remotas de baixa resolução."""
    if largura_tela <= 0 or altura_tela <= 0:
        raise ValueError("As dimensões da tela devem ser positivas.")

    margem_horizontal = 80 if largura_tela >= 800 else 20
    margem_vertical = 80 if altura_tela >= 700 else 40
    largura_disponivel = max(1, largura_tela - margem_horizontal)
    altura_disponivel = max(1, altura_tela - margem_vertical)

    largura = min(largura_tela, max(480, min(900, largura_disponivel)))
    altura = min(altura_tela, max(420, min(900, altura_disponivel)))
    return largura, altura


def calcular_limite_descriptografia(tamanho_bytes: int) -> int:
    """Calcula um timeout entre 30 minutos e 12 horas conforme o tamanho do GPG."""
    if tamanho_bytes < 0:
        raise ValueError("O tamanho do arquivo não pode ser negativo.")
    gibibytes = tamanho_bytes / (1024 ** 3)
    return min(12 * 60 * 60, max(30 * 60, int(gibibytes * 10 * 60)))


def parametros_retomada_http(
    tamanho_parcial: int,
    status_http: int,
    content_range: str,
    content_length: int,
) -> tuple[str, int, int]:
    """Determina gravação, posição inicial e tamanho final anunciado."""
    if tamanho_parcial < 0 or content_length < 0:
        raise ValueError("Os tamanhos HTTP não podem ser negativos.")
    if status_http == 206:
        correspondencia = re.fullmatch(
            r"bytes\s+(\d+)-(\d+)/(\d+)",
            content_range.strip(),
            flags=re.IGNORECASE,
        )
        if not correspondencia:
            raise ValueError("O servidor retornou Content-Range ausente ou inválido.")
        inicio, fim, total = (int(valor) for valor in correspondencia.groups())
        if inicio != tamanho_parcial:
            raise ValueError("O servidor retornou uma faixa diferente da solicitada.")
        if fim < inicio or fim >= total:
            raise ValueError("O servidor retornou limites inválidos em Content-Range.")
        tamanho_faixa = fim - inicio + 1
        if content_length and content_length != tamanho_faixa:
            raise ValueError("Content-Length não corresponde à faixa HTTP recebida.")
        return "ab", inicio, total
    if status_http == 200:
        return "wb", 0, content_length
    raise ValueError(f"Status HTTP inesperado para download: {status_http}.")


def validar_tamanho_download(tamanho_recebido: int, tamanho_esperado: int) -> bool:
    """Garante que um download com tamanho anunciado terminou exatamente no byte esperado."""
    if tamanho_recebido < 0 or tamanho_esperado < 0:
        raise ValueError("Os tamanhos do download não podem ser negativos.")
    if tamanho_esperado == 0:
        return False
    if tamanho_recebido != tamanho_esperado:
        raise ValueError(
            f"Download incompleto: recebidos {tamanho_recebido} de {tamanho_esperado} bytes."
        )
    return True


def tamanho_total_resposta_http(
    status_code: int,
    content_length: int | None,
    content_range: str = "",
) -> int | None:
    """Obtém o tamanho integral anunciado por HEAD ou por uma requisição de faixa."""
    if content_length is not None and content_length < 0:
        raise ValueError("Content-Length não pode ser negativo.")
    faixa = content_range.strip()
    if status_code == 206:
        correspondencia = re.fullmatch(r"bytes\s+\d+-\d+/(\d+)", faixa, re.I)
        if not correspondencia:
            raise ValueError("Content-Range ausente ou inválido na consulta de tamanho.")
        return int(correspondencia.group(1))
    if status_code == 416:
        correspondencia = re.fullmatch(r"bytes\s+\*/(\d+)", faixa, re.I)
        if not correspondencia:
            raise ValueError("Content-Range inválido na resposta 416.")
        return int(correspondencia.group(1))
    if 200 <= status_code < 300 and content_length is not None:
        return content_length
    return None


def formatar_tamanho_bytes(tamanho: int) -> str:
    """Formata bytes para logs sem perder o valor exato usado na auditoria."""
    if tamanho < 0:
        raise ValueError("O tamanho não pode ser negativo.")
    unidades = ("bytes", "KB", "MB", "GB", "TB")
    valor = float(tamanho)
    unidade = unidades[0]
    for unidade in unidades:
        if valor < 1024 or unidade == unidades[-1]:
            break
        valor /= 1024
    if unidade == "bytes":
        return f"{tamanho} bytes"
    return f"{valor:.2f} {unidade} ({tamanho} bytes)"


def nome_download_estavel(id_produto: str, url: str, extensao: str = ".gpg") -> str:
    """Gera o mesmo nome para um link em execuções e retomadas diferentes."""
    produto = nome_arquivo_seguro(id_produto, limite=70)
    digest = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()[:12]
    extensao = extensao if extensao.startswith(".") else f".{extensao}"
    return f"{produto}_{digest}{extensao}"


def resumir_etapas_links(links: list[str], etapas: dict[str, str]) -> str:
    """Resume o estado real de um produto usando somente os links daquela conta."""
    total = len(links)
    if total == 0:
        return "Sem arquivos"

    estados = [etapas.get(link, "pendente") for link in links]
    concluidos = estados.count("concluido")
    recebidos = sum(estado not in ("pendente", "erro_download") for estado in estados)

    if concluidos == total:
        return "✅ Concluído"
    if "erro_extracao" in estados:
        return f"❌ Erro na extração ({concluidos}/{total} concluídos)"
    if "erro_descriptografia" in estados:
        prontos = concluidos + estados.count("pronto_extracao")
        return f"❌ Erro na descriptografia ({prontos}/{total} concluídos)"
    if "erro_download" in estados:
        return f"❌ Erro no download ({recebidos}/{total} recebidos)"
    if "pronto_extracao" in estados:
        prontos = concluidos + estados.count("pronto_extracao")
        return f"🔐 Pronto para extração ({prontos}/{total})"
    if recebidos == total:
        return "✅ Download concluído"
    if recebidos:
        return f"⬇️ Baixando ({recebidos}/{total})"
    return "⏳ Pendente"


def resource_path(relative_path: str | os.PathLike[str]) -> str:
    """Resolve um recurso no código-fonte ou no diretório temporário do PyInstaller."""
    caminho = Path(relative_path)
    if caminho.is_absolute():
        return str(caminho)

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / caminho)


def pasta_base_execucao() -> str:
    """Retorna uma pasta previsível e gravável ao lado do executável ou do código."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent)
    return str(Path(__file__).resolve().parent)


def validar_url_https(url: str) -> bool:
    """Aceita somente URLs HTTPS completas, sem credenciais embutidas."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    return (
        parsed.scheme.lower() == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def url_segura_para_log(url: str) -> str:
    """Registra somente esquema e host, ocultando tokens presentes no caminho."""
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(url.strip())
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return f"{parsed.scheme}://{host}/[caminho-oculto]"
    except ValueError:
        return "URL inválida"


class _ColetorLinksProdutos(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.links_detalhados: list[dict[str, str]] = []
        self._em_titulo_produto = False
        self._partes_titulo: list[str] = []
        self._titulo_produto_atual = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "h3":
            self._em_titulo_produto = True
            self._partes_titulo = []
            return
        if tag.lower() != "a":
            return
        atributos = {nome.lower(): valor for nome, valor in attrs if valor is not None}
        id_tag = atributos.get("id", "").strip()
        link = atributos.get("href", "").strip()
        if id_tag and validar_url_https(link):
            self.links.append((id_tag, link))
            self.links_detalhados.append(
                {
                    "produto": id_tag,
                    "url": link,
                    "nome_produto": self._titulo_produto_atual,
                    "nome_arquivo_original": nome_arquivo_original_url(link),
                }
            )

    def handle_data(self, data: str) -> None:
        if self._em_titulo_produto:
            self._partes_titulo.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "h3" or not self._em_titulo_produto:
            return
        self._titulo_produto_atual = " ".join("".join(self._partes_titulo).split())
        self._em_titulo_produto = False
        self._partes_titulo = []


def nome_arquivo_original_url(url: str) -> str:
    """Obtém o nome original do pacote Apple sem consulta, hash ou renomeação."""
    if not validar_url_https(url):
        raise ValueError("A URL do pacote não é HTTPS válida.")
    nome = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    if not nome:
        raise ValueError("A URL do pacote não contém nome de arquivo.")
    return _componente_zip_exato(nome)


def nome_pasta_produto(id_produto: str, titulo_manifesto: str = "") -> str:
    """Escolhe o título legível do manifesto para organizar um produto Apple."""
    titulo = " ".join(titulo_manifesto.split())
    if titulo:
        return nome_arquivo_seguro(titulo, limite=150)

    identificador = re.sub(r"links$", "", id_produto.strip(), flags=re.IGNORECASE)
    legivel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", identificador)
    legivel = re.sub(r"^i Cloud\b", "iCloud", legivel)
    return nome_arquivo_seguro(legivel or id_produto, limite=150)


def extrair_links_html(conteudo: str) -> list[tuple[str, str]]:
    """Coleta links HTTPS identificados, independentemente da ordem dos atributos."""
    coletor = _ColetorLinksProdutos()
    coletor.feed(conteudo)
    coletor.close()
    return coletor.links


def extrair_links_html_detalhados(conteudo: str) -> list[dict[str, str]]:
    """Coleta URL, produto, título visível e nome original informado pela Apple."""
    coletor = _ColetorLinksProdutos()
    coletor.feed(conteudo)
    coletor.close()
    return coletor.links_detalhados


def nome_arquivo_seguro(valor: str, limite: int = 100) -> str:
    """Converte um identificador externo em um componente de arquivo seguro e estável."""
    original = valor.strip()
    seguro = _CARACTERES_INVALIDOS.sub("_", original).strip(" .")
    if not seguro or seguro.split(".", 1)[0].upper() in _NOMES_RESERVADOS_WINDOWS:
        seguro = "item"

    alterado = seguro != original or len(seguro) > limite
    seguro = seguro[:limite].rstrip(" .") or "item"
    if alterado:
        digest = hashlib.sha256(original.encode("utf-8", errors="replace")).hexdigest()[:8]
        seguro = f"{seguro[: max(1, limite - 9)]}_{digest}"
    return seguro


def pasta_saida_segura(base: str | os.PathLike[str], nome: str) -> str:
    """Cria um caminho filho seguro para o analista, sem permitir fuga da pasta base."""
    nome_seguro = nome_arquivo_seguro(nome, limite=80)
    base_resolvida = Path(base).resolve()
    destino = (base_resolvida / nome_seguro).resolve()
    if os.path.commonpath((str(base_resolvida), str(destino))) != str(base_resolvida):
        raise ValueError("O nome do analista resulta em um caminho inválido.")
    return str(destino)


def localizar_gpg() -> str | None:
    """Localiza o GPG configurado, empacotado com o aplicativo ou disponível no PATH."""
    candidatos: list[Path] = []

    configurado = os.environ.get("NEMESIS_GPG_PATH")
    if configurado:
        candidatos.append(Path(configurado))

    raiz_execucao = Path(sys.executable).resolve().parent
    raiz_projeto = Path(__file__).resolve().parent
    for raiz in (Path(resource_path(".")), raiz_execucao, raiz_projeto):
        candidatos.extend(
            (
                raiz / "gpg" / "bin" / "gpg.exe",
                raiz / "gnupg" / "bin" / "gpg.exe",
                raiz / "gnupg-2.4.7" / "bin" / "gpg.exe",
            )
        )

    for candidato in candidatos:
        if candidato.is_file():
            return str(candidato)

    return shutil.which("gpg") or shutil.which("gpg2")


def _componente_zip_exato(componente: str, limite: int = _LIMITE_COMPONENTE_ZIP) -> str:
    """Aceita somente nomes que possam ser preservados exatamente no Windows."""
    if (
        not componente
        or _CARACTERES_INVALIDOS.search(componente)
        or componente.endswith((" ", "."))
        or componente.split(".", 1)[0].upper() in _NOMES_RESERVADOS_WINDOWS
        or len(componente.encode("utf-16-le")) // 2 > limite
    ):
        raise NomeZipIncompativel(
            f"O nome '{componente}' não pode ser preservado exatamente no Windows. "
            "O ZIP foi mantido para extração em um sistema compatível."
        )
    return componente


def _caminho_estendido_windows(caminho: Path) -> str:
    """Permite I/O em caminhos longos sem alterar a estrutura do ZIP."""
    texto = os.path.abspath(str(caminho))
    if os.name != "nt" or texto.startswith("\\\\?\\"):
        return texto
    if texto.startswith("\\\\"):
        return "\\\\?\\UNC\\" + texto.lstrip("\\")
    return "\\\\?\\" + texto


def _destino_membro_zip(
    pasta_destino: Path,
    nome_membro: str,
) -> tuple[Path, list[str]]:
    nome_normalizado = nome_membro.replace("\\", "/")
    partes_originais = [parte for parte in nome_normalizado.split("/") if parte not in ("", ".")]

    if (
        any(parte == ".." for parte in partes_originais)
        or (partes_originais and re.match(r"^[A-Za-z]:", partes_originais[0]))
    ):
        raise ValueError(f"Caminho inseguro dentro do ZIP: {nome_membro}")
    if not partes_originais:
        raise ValueError(f"Caminho vazio dentro do ZIP: {nome_membro}")

    partes = [_componente_zip_exato(parte) for parte in partes_originais]
    motivos: list[str] = []
    if nome_normalizado.startswith("/"):
        motivos.append("raiz_removida")
    destino = pasta_destino.joinpath(*partes).resolve()
    if os.path.commonpath((str(pasta_destino), str(destino))) != str(pasta_destino):
        raise ValueError(f"Caminho fora da pasta de destino: {nome_membro}")
    return destino, motivos


def _salvar_mapa_extracao(
    caminho_zip: str | os.PathLike[str],
    registros: list[dict[str, object]],
    sha256_zip: str | None = None,
) -> str | None:
    if not registros:
        return None
    nome_zip = Path(caminho_zip).name
    digest = hashlib.sha256(nome_zip.encode("utf-8", errors="replace")).hexdigest()[:16]
    # O mapa é evidência técnica, não conteúdo do arquivo. Ele fica como um
    # arquivo ao lado do ZIP temporário, sem criar pastas na árvore extraída.
    zip_resolvido = Path(caminho_zip).resolve()
    caminho_mapa = zip_resolvido.parent / f"{zip_resolvido.stem}.{digest}.nemesis-map.json"
    temporario = caminho_mapa.with_suffix(".json.tmp")
    dados = {"arquivo_zip": nome_zip, "itens_renomeados": registros}
    if sha256_zip:
        dados["sha256_zip"] = sha256_zip
    with temporario.open("w", encoding="utf-8", newline="\n") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2, sort_keys=True)
        arquivo.write("\n")
    os.replace(temporario, caminho_mapa)
    return str(caminho_mapa)


def _detectar_avisos_origem(caminho: Path, relativo: str) -> list[dict[str, object]]:
    """Detecta relatórios textuais em que a Apple declara falhas antes do download."""
    try:
        if caminho.suffix.lower() not in _EXTENSOES_TEXTO_AUDITAVEIS:
            return []
        caminho_io = _caminho_estendido_windows(caminho)
        tamanho = os.path.getsize(caminho_io)
        if tamanho <= 0 or tamanho > _LIMITE_RELATORIO_ORIGEM:
            return []
        with open(caminho_io, "r", encoding="utf-8", errors="replace") as arquivo:
            texto = arquivo.read()
    except OSError:
        return []

    posicao = texto.lower().find(_MENSAGEM_FALHA_ICLOUD)
    if posicao < 0:
        return []
    trecho = texto[posicao : posicao + 200_000]
    itens = re.findall(r"\[([^\]\r\n]{1,500})\]", trecho)
    return [
        {
            "origem": "Apple",
            "codigo": "APPLE_ICLOUD_ARCHIVE_CREATION_FAILED",
            "arquivo_relatorio": relativo,
            "mensagem": "A Apple informou falhas ao criar o arquivo de backup do iCloud.",
            "quantidade_itens": len(itens),
            "itens": itens,
        }
    ]


def _sha256_caminho(caminho: Path) -> str:
    digest = hashlib.sha256()
    with open(_caminho_estendido_windows(caminho), "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(4 * 1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest().upper()


def _sha256_membro_zip(arquivo_zip: zipfile.ZipFile, membro: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with arquivo_zip.open(membro, "r") as origem:
        for bloco in iter(lambda: origem.read(4 * 1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest().upper()


def _manifesto_zip(membros: list[zipfile.ZipInfo]) -> str:
    """Resume caminhos, tamanhos e CRCs do ZIP sem alterar a ordem da evidência."""
    digest = hashlib.sha256()
    for membro in membros:
        if membro.is_dir():
            continue
        linha = f"{membro.filename}\0{membro.file_size}\0{membro.CRC:08X}\n"
        digest.update(linha.encode("utf-8", errors="surrogatepass"))
    return digest.hexdigest().upper()


def auditar_pasta_extraida(pasta: str | os.PathLike[str]) -> dict[str, object]:
    """Inventaria uma extração existente e procura avisos declarados pela origem."""
    raiz = Path(pasta).resolve()
    if not raiz.is_dir():
        raise FileNotFoundError(f"Pasta de extração não encontrada: {raiz}")
    arquivos = 0
    bytes_encontrados = 0
    avisos_origem: list[dict[str, object]] = []
    erros_leitura: list[dict[str, str]] = []
    raiz_io = _caminho_estendido_windows(raiz)
    for pasta_atual, pastas, nomes in os.walk(raiz_io):
        # Ignora mapas deixados dentro da extração por versões anteriores.
        pastas[:] = [pasta for pasta in pastas if pasta != _PASTA_MAPAS_EXTRACAO_LEGADA]
        for nome in nomes:
            caminho = Path(pasta_atual) / nome
            try:
                relativo = os.path.relpath(str(caminho), raiz_io).replace("\\", "/")
                tamanho = os.path.getsize(_caminho_estendido_windows(caminho))
                arquivos += 1
                bytes_encontrados += tamanho
                avisos_origem.extend(_detectar_avisos_origem(caminho, relativo))
            except OSError as erro:
                erros_leitura.append(
                    {
                        "arquivo": str(caminho),
                        "erro": type(erro).__name__,
                    }
                )
    return {
        "extraidos": arquivos,
        "bytes_extraidos": bytes_encontrados,
        "renomeados": 0,
        "avisos_origem": avisos_origem,
        "erros_leitura": erros_leitura,
        "auditoria_pos_execucao": True,
    }


def extrair_zip_seguro(
    caminho_zip: str | os.PathLike[str],
    pasta_destino: str | os.PathLike[str],
    relatorio: dict[str, object] | None = None,
) -> int:
    """Extrai ZIPs Apple com nomes Windows seguros, sem permitir fuga do destino."""
    destino_raiz = Path(pasta_destino).resolve()
    os.makedirs(_caminho_estendido_windows(destino_raiz), exist_ok=True)
    extraidos = 0
    bytes_extraidos = 0
    arquivos_reutilizados = 0
    bytes_reutilizados = 0
    registros: list[dict[str, object]] = []
    avisos_origem: list[dict[str, object]] = []

    with zipfile.ZipFile(caminho_zip, "r") as arquivo_zip:
        membros = arquivo_zip.infolist()
        itens: list[tuple[zipfile.ZipInfo, Path, list[str], bool]] = []
        destinos_vistos: dict[str, tuple[str, zipfile.ZipInfo]] = {}

        # Valida toda a árvore e todos os conflitos antes de gravar o primeiro byte.
        for membro in membros:
            modo = membro.external_attr >> 16
            if stat.S_ISLNK(modo):
                raise ValueError(f"Link simbólico não permitido no ZIP: {membro.filename}")

            destino, motivos = _destino_membro_zip(destino_raiz, membro.filename)
            relativo = destino.relative_to(destino_raiz).as_posix()
            chave = relativo.casefold() if os.name == "nt" else relativo
            anterior = destinos_vistos.get(chave)
            duplicado_interno = False
            if anterior is not None:
                nome_anterior, membro_anterior = anterior
                if nome_anterior != relativo:
                    raise FileExistsError(
                        "O ZIP contém caminhos que diferem apenas por maiúsculas/minúsculas "
                        f"e não podem coexistir no Windows: {nome_anterior} e {relativo}."
                    )
                if (membro_anterior.is_dir() != membro.is_dir()
                        or membro_anterior.file_size != membro.file_size or membro_anterior.CRC != membro.CRC
                        or (not membro.is_dir() and _sha256_membro_zip(arquivo_zip, membro_anterior)
                            != _sha256_membro_zip(arquivo_zip, membro))):
                    raise FileExistsError(
                        f"O ZIP contém dois conteúdos diferentes no mesmo caminho: {relativo}."
                    )
                duplicado_interno = True
            else:
                destinos_vistos[chave] = (relativo, membro)

            if motivos:
                registros.append(
                    {
                        "original": membro.filename,
                        "extraido": relativo,
                        "motivos": motivos,
                    }
                )
            itens.append((membro, destino, motivos, duplicado_interno))

        preexistentes_identicos: set[str] = set()
        for membro, destino, _, duplicado_interno in itens:
            if membro.is_dir() or duplicado_interno:
                continue
            destino_io = _caminho_estendido_windows(destino)
            if not os.path.exists(destino_io):
                continue
            if not os.path.isfile(destino_io):
                raise FileExistsError(f"O caminho de arquivo já existe como pasta: {membro.filename}")
            if os.path.getsize(destino_io) != membro.file_size:
                raise FileExistsError(
                    f"Conflito: já existe conteúdo diferente no caminho original {membro.filename}. "
                    "Nenhum arquivo será substituído."
                )
            if _sha256_caminho(destino) != _sha256_membro_zip(arquivo_zip, membro):
                raise FileExistsError(
                    f"Conflito: já existe conteúdo diferente no caminho original {membro.filename}. "
                    "Nenhum arquivo será substituído."
                )
            preexistentes_identicos.add(destino_io)

        for membro, destino, _, duplicado_interno in itens:
            if membro.is_dir():
                os.makedirs(_caminho_estendido_windows(destino), exist_ok=True)
                continue

            if duplicado_interno or _caminho_estendido_windows(destino) in preexistentes_identicos:
                arquivos_reutilizados += 1
                bytes_reutilizados += membro.file_size
                continue

            os.makedirs(_caminho_estendido_windows(destino.parent), exist_ok=True)
            destino_io = _caminho_estendido_windows(destino)
            temporario = destino.parent / (
                f".__nemesis_{hashlib.sha256(str(destino).encode('utf-8')).hexdigest()[:16]}.part"
            )
            temporario_io = _caminho_estendido_windows(temporario)
            try:
                with arquivo_zip.open(membro, "r") as origem, open(temporario_io, "xb") as saida:
                    shutil.copyfileobj(origem, saida, length=1024 * 1024)
                tamanho_extraido = os.path.getsize(temporario_io)
                if tamanho_extraido != membro.file_size:
                    raise OSError(
                        f"Tamanho extraído divergente para {membro.filename}: "
                        f"{tamanho_extraido} de {membro.file_size} bytes."
                    )
                os.replace(temporario_io, destino_io)
            except Exception:
                try:
                    os.remove(temporario_io)
                except OSError:
                    pass
                raise
            extraidos += 1
            bytes_extraidos += tamanho_extraido
            relativo_extraido = destino.relative_to(destino_raiz).as_posix()
            avisos_origem.extend(_detectar_avisos_origem(destino, relativo_extraido))

    caminho_mapa = _salvar_mapa_extracao(caminho_zip, registros)
    if relatorio is not None:
        relatorio.clear()
        relatorio.update(
            {
                "extraidos": extraidos,
                "bytes_extraidos": bytes_extraidos,
                "arquivos_reutilizados_identicos": arquivos_reutilizados,
                "bytes_reutilizados_identicos": bytes_reutilizados,
                "arquivos_manifestados_zip": sum(not membro.is_dir() for membro in membros),
                "bytes_manifestados_zip": sum(membro.file_size for membro in membros if not membro.is_dir()),
                "bytes_zip_compactado": os.path.getsize(caminho_zip),
                "sha256_manifesto_zip": _manifesto_zip(membros),
                "renomeados": len(registros),
                "mapa": caminho_mapa,
                "avisos_origem": avisos_origem,
            }
        )
    return extraidos


def recuperar_zip_com_mapa(
    caminho_zip: str | os.PathLike[str], pasta_destino: str | os.PathLike[str],
    relatorio: dict[str, object] | None = None,
) -> int:
    """Recupera cada entrada em pasta isolada, sem sobrescrever, com mapa e hashes.

    O ZIP original deve ser mantido pelo chamador. Entradas inseguras continuam bloqueadas.
    """
    raiz = Path(pasta_destino).resolve()
    sha_zip = _sha256_caminho(Path(caminho_zip))
    pasta = raiz / "_nemesis_recuperados" / sha_zip
    if os.path.commonpath((str(raiz), str(pasta.resolve()))) != str(raiz):
        raise ValueError("A pasta de recuperação aponta para fora do destino.")
    os.makedirs(_caminho_estendido_windows(pasta), exist_ok=True)
    registros = []
    avisos = []
    gravados = reutilizados = bytes_gravados = bytes_reutilizados = 0
    with zipfile.ZipFile(caminho_zip) as arquivo_zip:
        membros = arquivo_zip.infolist()
        # A recuperação nunca deve contornar a proteção contra caminhos maliciosos.
        for membro in membros:
            partes = [p for p in membro.filename.replace("\\", "/").split("/") if p not in ("", ".")]
            if (not partes or ".." in partes or re.match(r"^[A-Za-z]:", partes[0])
                    or stat.S_ISLNK(membro.external_attr >> 16)):
                raise ValueError(f"Entrada insegura no ZIP: {membro.filename}")
        for indice, membro in enumerate(membros):
            if membro.is_dir():
                continue
            sufixo = Path(membro.filename.replace("\\", "/")).suffix
            if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", sufixo):
                sufixo = ""
            destino = pasta / f"{indice:08d}{sufixo}"
            destino_io = _caminho_estendido_windows(destino)
            if os.path.lexists(destino_io):
                if not os.path.isfile(destino_io) or os.path.islink(destino_io):
                    raise FileExistsError(f"Destino de recuperação ocupado: {destino}")
                sha = _sha256_membro_zip(arquivo_zip, membro)
                if os.path.getsize(destino_io) != membro.file_size or _sha256_caminho(destino) != sha:
                    raise FileExistsError(f"Conteúdo divergente na recuperação: {destino}")
                reutilizados += 1
                bytes_reutilizados += membro.file_size
            else:
                digest = hashlib.sha256()
                temporario_io = None
                try:
                    fd, temporario_io = tempfile.mkstemp(prefix=".__nemesis_rec_", suffix=".part", dir=_caminho_estendido_windows(pasta))
                    with os.fdopen(fd, "wb") as saida, arquivo_zip.open(membro) as origem:
                        for bloco in iter(lambda: origem.read(4 * 1024 * 1024), b""):
                            saida.write(bloco)
                            digest.update(bloco)
                    if os.path.getsize(temporario_io) != membro.file_size:
                        raise OSError(f"Tamanho divergente na recuperação: {membro.filename}")
                    sha = digest.hexdigest().upper()
                    if _sha256_caminho(Path(temporario_io)) != sha:
                        raise OSError(f"Hash divergente na recuperação: {membro.filename}")
                    if os.name == "nt":
                        os.rename(temporario_io, destino_io)
                        temporario_io = None
                    else:
                        os.link(temporario_io, destino_io)
                finally:
                    if temporario_io:
                        try:
                            os.remove(temporario_io)
                        except OSError:
                            pass
                gravados += 1
                bytes_gravados += membro.file_size
            relativo = destino.relative_to(raiz).as_posix()
            registros.append({"indice_zip": indice, "original": membro.filename,
                              "extraido": relativo, "bytes": membro.file_size, "sha256": sha})
            avisos.extend(_detectar_avisos_origem(destino, membro.filename))

    mapa = _salvar_mapa_extracao(caminho_zip, registros, sha_zip)
    if relatorio is not None:
        relatorio.clear()
        relatorio.update({"extraidos": gravados, "bytes_extraidos": bytes_gravados,
            "arquivos_reutilizados_identicos": reutilizados, "bytes_reutilizados_identicos": bytes_reutilizados,
            "arquivos_manifestados_zip": sum(not m.is_dir() for m in membros),
            "bytes_manifestados_zip": sum(m.file_size for m in membros if not m.is_dir()),
            "bytes_zip_compactado": os.path.getsize(caminho_zip), "sha256_zip": sha_zip,
            "sha256_manifesto_zip": _manifesto_zip(membros), "renomeados": len(registros),
            "mapa": mapa, "recuperacao_isolada": True, "zip_preservado": str(Path(caminho_zip).resolve()),
            "sha256_mapa": _sha256_caminho(Path(mapa)) if mapa else None,
            "avisos_origem": avisos})
    return gravados
