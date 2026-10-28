import os
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from nemesis_core import (
    auditar_pasta_extraida,
    calcular_limite_descriptografia,
    calcular_tamanho_janela,
    extrair_links_html,
    extrair_links_html_detalhados,
    extrair_zip_seguro,
    recuperar_zip_com_mapa,
    formatar_tamanho_bytes,
    nome_download_estavel,
    nome_arquivo_seguro,
    nome_arquivo_original_url,
    nome_pasta_produto,
    pasta_base_execucao,
    pasta_saida_segura,
    parametros_retomada_http,
    resumir_etapas_links,
    tamanho_total_resposta_http,
    url_segura_para_log,
    validar_tamanho_download,
    validar_url_https,
)


class NemesisCoreTests(unittest.TestCase):
    def test_recuperacao_preserva_entradas_duplicadas_e_nomes_incompativeis(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo, destino = raiz / "origem.zip", raiz / "saida"
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("Fotos/igual.jpg", b"primeira")
                zipf.writestr("Fotos/igual.jpg", b"segunda")
                zipf.writestr("Contatos/nome:invalido.vcf", b"contato")
                zipf.writestr("backup/FailedFileList.txt", "The following files failed when creating the iCloud backup data archive:\n[N_FALHOU]")
            relatorio = {}
            self.assertEqual(recuperar_zip_com_mapa(arquivo, destino, relatorio), 4)
            mapa = json.loads(Path(relatorio["mapa"]).read_text(encoding="utf-8"))
            itens = mapa["itens_renomeados"]
            self.assertEqual([item["original"] for item in itens[:3]], ["Fotos/igual.jpg", "Fotos/igual.jpg", "Contatos/nome:invalido.vcf"])
            self.assertEqual([(destino / item["extraido"]).read_bytes() for item in itens[:3]], [b"primeira", b"segunda", b"contato"])
            self.assertTrue(all(len(item["sha256"]) == 64 for item in itens))
            self.assertEqual(relatorio["avisos_origem"][0]["itens"], ["N_FALHOU"])
            self.assertTrue(arquivo.exists())
            retomada = {}
            self.assertEqual(recuperar_zip_com_mapa(arquivo, destino, retomada), 0)
            self.assertEqual(retomada["arquivos_reutilizados_identicos"], 4)

    def test_recuperacao_bloqueia_entrada_insegura_sem_gravar_arquivos(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo, destino = raiz / "origem.zip", raiz / "saida"
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("valido.txt", "ok")
                zipf.writestr("../fora.txt", "não")
            with self.assertRaisesRegex(ValueError, "Entrada insegura"):
                recuperar_zip_com_mapa(arquivo, destino)
            self.assertFalse((raiz / "fora.txt").exists())
            self.assertFalse(list(destino.rglob("*.txt")))

    def test_recuperacao_nao_substitui_conteudo_preexistente_divergente(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo, destino = raiz / "origem.zip", raiz / "saida"
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("arquivo.txt", "primeiro")
            relatorio = {}
            recuperar_zip_com_mapa(arquivo, destino, relatorio)
            item = json.loads(Path(relatorio["mapa"]).read_text(encoding="utf-8"))["itens_renomeados"][0]
            recuperado = destino / item["extraido"]
            recuperado.write_text("alterado")
            with self.assertRaisesRegex(FileExistsError, "Conteúdo divergente"):
                recuperar_zip_com_mapa(arquivo, destino)
            self.assertEqual(recuperado.read_text(), "alterado")

    def test_obtem_tamanho_remoto_por_head_ou_content_range(self):
        self.assertEqual(tamanho_total_resposta_http(200, 31_130_000_000), 31_130_000_000)
        self.assertEqual(
            tamanho_total_resposta_http(206, 1, "bytes 0-0/31130000000"),
            31_130_000_000,
        )
        self.assertEqual(
            tamanho_total_resposta_http(416, 0, "bytes */31130000000"),
            31_130_000_000,
        )
        self.assertIsNone(tamanho_total_resposta_http(200, None))
        with self.assertRaises(ValueError):
            tamanho_total_resposta_http(206, 1, "invalido")

    def test_timeout_gpg_cresce_com_o_arquivo_e_tem_limites_seguros(self):
        self.assertEqual(calcular_limite_descriptografia(0), 30 * 60)
        self.assertEqual(calcular_limite_descriptografia(10 * 1024 ** 3), 100 * 60)
        self.assertEqual(calcular_limite_descriptografia(1024 * 1024 ** 3), 12 * 60 * 60)
        with self.assertRaises(ValueError):
            calcular_limite_descriptografia(-1)

    def test_resposta_206_continua_do_tamanho_parcial(self):
        modo, inicio, total = parametros_retomada_http(
            3,
            206,
            "bytes 3-5/6",
            3,
        )
        self.assertEqual((modo, inicio, total), ("ab", 3, 6))

    def test_servidor_sem_range_reinicia_somente_aquele_arquivo(self):
        modo, inicio, total = parametros_retomada_http(3, 200, "", 6)
        self.assertEqual((modo, inicio, total), ("wb", 0, 6))

    def test_rejeita_faixa_http_inconsistente(self):
        with self.assertRaises(ValueError):
            parametros_retomada_http(3, 206, "bytes 4-6/7", 3)

    def test_rejeita_content_length_diferente_da_faixa(self):
        with self.assertRaises(ValueError):
            parametros_retomada_http(3, 206, "bytes 3-5/6", 2)

    def test_rejeita_resposta_parcial_sem_content_range_valido(self):
        with self.assertRaises(ValueError):
            parametros_retomada_http(3, 206, "", 3)

    def test_download_so_e_verificado_com_tamanho_exato(self):
        self.assertTrue(validar_tamanho_download(6, 6))
        self.assertFalse(validar_tamanho_download(6, 0))
        with self.assertRaisesRegex(ValueError, "recebidos 5 de 6 bytes"):
            validar_tamanho_download(5, 6)
        with self.assertRaisesRegex(ValueError, "recebidos 7 de 6 bytes"):
            validar_tamanho_download(7, 6)

    def test_formata_tamanho_preservando_bytes_exatos(self):
        self.assertEqual(formatar_tamanho_bytes(512), "512 bytes")
        self.assertEqual(formatar_tamanho_bytes(1024), "1.00 KB (1024 bytes)")

    def test_nome_download_e_estavel_e_nao_expoe_url(self):
        url = "https://exemplo.gov.br/arquivo.gpg?token=secreto"
        primeiro = nome_download_estavel("iCloud/Drive", url)
        segundo = nome_download_estavel("iCloud/Drive", url)
        self.assertEqual(primeiro, segundo)
        self.assertTrue(primeiro.endswith(".gpg"))
        self.assertNotIn("token", primeiro)
        self.assertNotIn("/", primeiro)

    def test_status_do_produto_considera_somente_links_da_conta(self):
        links_conta = ["https://exemplo/a", "https://exemplo/b"]
        etapas = {
            "https://exemplo/a": "concluido",
            "https://exemplo/b": "erro_extracao",
            "https://outra-conta/c": "concluido",
        }
        self.assertEqual(
            resumir_etapas_links(links_conta, etapas),
            "❌ Erro na extração (1/2 concluídos)",
        )

    def test_completo_exige_todos_os_links_extraidos(self):
        links = ["a", "b"]
        self.assertEqual(
            resumir_etapas_links(links, {"a": "concluido", "b": "concluido"}),
            "✅ Concluído",
        )
        self.assertNotIn(
            "Concluído",
            resumir_etapas_links(links, {"a": "concluido", "b": "baixado"}),
        )

    def test_janela_cabe_em_sessao_remota_800x600(self):
        largura, altura = calcular_tamanho_janela(800, 600)
        self.assertLessEqual(largura, 800)
        self.assertLessEqual(altura, 600)
        self.assertEqual((largura, altura), (720, 560))

    def test_janela_limita_tamanho_em_tela_grande(self):
        self.assertEqual(calcular_tamanho_janela(1920, 1080), (900, 900))

    def test_dimensoes_invalidas_sao_rejeitadas(self):
        with self.assertRaises(ValueError):
            calcular_tamanho_janela(0, 600)

    def test_url_deve_ser_https_com_host(self):
        self.assertTrue(validar_url_https("https://exemplo.gov.br/arquivo.gpg?token=123"))
        self.assertFalse(validar_url_https("http://exemplo.gov.br/arquivo.gpg"))
        self.assertFalse(validar_url_https("arquivo.gpg"))
        self.assertFalse(validar_url_https("https://usuario:senha@exemplo.gov.br/arquivo"))
        self.assertEqual(
            url_segura_para_log("https://usuario:senha@exemplo.gov.br/arquivo?token=secreto"),
            "https://exemplo.gov.br/[caminho-oculto]",
        )

    def test_links_html_independem_da_ordem_dos_atributos(self):
        conteudo = """
        <a href="https://exemplo.gov.br/a.gpg?token=1" id="produto-a">A</a>
        <a id="produto-b" class="arquivo" href="https://exemplo.gov.br/b.gpg">B</a>
        <a id="inseguro" href="http://exemplo.gov.br/c.gpg">C</a>
        """
        self.assertEqual(
            extrair_links_html(conteudo),
            [
                ("produto-a", "https://exemplo.gov.br/a.gpg?token=1"),
                ("produto-b", "https://exemplo.gov.br/b.gpg"),
            ],
        )

    def test_manifesto_fornece_pasta_legivel_e_nome_original_do_pacote(self):
        conteudo = """
        <h3>iCloud Reminders</h3>
        <table><tr><td><a id="iCloudReminderslinks"
          href="https://apple.example/2354676-ICLOUDREMINDERS-41489874.zip.gpg?token=1">
          2354676-ICLOUDREMINDERS-41489874.zip.gpg</a></td></tr></table>
        <h3>Safari Browsing History</h3>
        <a href="https://apple.example/2354676-SAFARIBROWSINGHISTORY-41489871.zip.gpg"
          id="SafariBrowsingHistorylinks">pacote</a>
        <h3>Sign in with Apple</h3>
        <a id="SigninwithApplelinks"
          href="https://apple.example/2354676-SIGNINWITHAPPLE-41489878.zip.gpg">pacote</a>
        """

        itens = extrair_links_html_detalhados(conteudo)

        self.assertEqual(
            [item["nome_produto"] for item in itens],
            ["iCloud Reminders", "Safari Browsing History", "Sign in with Apple"],
        )
        self.assertEqual(
            itens[0]["nome_arquivo_original"],
            "2354676-ICLOUDREMINDERS-41489874.zip.gpg",
        )
        self.assertEqual(nome_pasta_produto(itens[0]["produto"], itens[0]["nome_produto"]), "iCloud Reminders")

    def test_nome_original_da_url_nao_recebe_hash_nem_e_alterado(self):
        nome = nome_arquivo_original_url(
            "https://apple.example/pasta/2354676-ICLOUDREMINDERS-41489874.zip.gpg?segredo=1"
        )
        self.assertEqual(nome, "2354676-ICLOUDREMINDERS-41489874.zip.gpg")

    def test_nome_de_pasta_tem_fallback_legivel_para_estado_antigo(self):
        self.assertEqual(nome_pasta_produto("iCloudReminderslinks"), "iCloud Reminders")
        self.assertEqual(nome_pasta_produto("SafariBrowsingHistorylinks"), "Safari Browsing History")

    def test_nome_externo_nao_pode_criar_subpastas(self):
        nome = nome_arquivo_seguro(r"..\produto/invalido")
        self.assertNotIn("/", nome)
        self.assertNotIn("\\", nome)
        self.assertNotEqual(nome, "..")

    def test_pasta_do_analista_permanece_dentro_da_base(self):
        with tempfile.TemporaryDirectory() as pasta:
            base = Path(pasta).resolve()
            destino = Path(pasta_saida_segura(pasta, r"..\Analista/Teste")).resolve()
            self.assertEqual(os.path.commonpath((str(base), str(destino))), str(base))

    def test_pasta_base_do_codigo_nao_depende_do_diretorio_atual(self):
        esperado = Path(__file__).resolve().parents[1]
        self.assertEqual(Path(pasta_base_execucao()).resolve(), esperado)

    def test_extrai_zip_valido_preservando_subpastas(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo = raiz / "valido.zip"
            destino = raiz / "saida"
            relatorio = {}
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("conta/documento.txt", "conteudo")

            quantidade = extrair_zip_seguro(arquivo, destino, relatorio)

            self.assertEqual(quantidade, 1)
            self.assertEqual(relatorio["bytes_extraidos"], len("conteudo"))
            self.assertEqual((destino / "conta" / "documento.txt").read_text(), "conteudo")

    def test_extrai_caminho_apple_iniciado_na_raiz(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo = raiz / "apple-raiz.zip"
            destino = raiz / "saida"
            relatorio = {}
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("/conta/documento.txt", "conteudo")

            quantidade = extrair_zip_seguro(arquivo, destino, relatorio)

            self.assertEqual(quantidade, 1)
            self.assertEqual((destino / "conta" / "documento.txt").read_text(), "conteudo")
            self.assertEqual(relatorio["renomeados"], 1)
            mapa = Path(relatorio["mapa"])
            self.assertTrue(mapa.is_file())
            dados = json.loads(mapa.read_text(encoding="utf-8"))
            self.assertEqual(dados["itens_renomeados"][0]["motivos"], ["raiz_removida"])

    def test_recusa_nome_que_nao_pode_ser_preservado_no_windows(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo = raiz / "nomes-apple.zip"
            destino = raiz / "saida"
            relatorio = {}
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("Contacts/*Corretora | RE:MAX*.vcf", "primeiro")
                zipf.writestr("Contacts/*Corretora ? RE:MAX*.vcf", "segundo")
                zipf.writestr("Drive/CON.txt", "reservado")

            with self.assertRaisesRegex(ValueError, "não pode ser preservado exatamente"):
                extrair_zip_seguro(arquivo, destino, relatorio)

            self.assertEqual(list(destino.rglob("*")), [])

    def test_preserva_caminho_longo_sem_criar_pasta_artificial(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo = raiz / "longo.zip"
            destino = raiz / ("saida_" + "x" * 70)
            relatorio = {}
            caminho_apple = "/".join(
                ["conta", "Drive"] + [f"pasta_{indice}_" + "a" * 75 for indice in range(5)]
                + ["documento_final.txt"]
            )
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr(caminho_apple, "conteudo longo")

            quantidade = extrair_zip_seguro(arquivo, destino, relatorio)

            self.assertEqual(quantidade, 1)
            esperado = destino.joinpath(*caminho_apple.split("/"))
            esperado_io = str(esperado.resolve())
            if os.name == "nt":
                esperado_io = "\\\\?\\" + esperado_io
            with open(esperado_io, "r", encoding="utf-8") as arquivo_extraido:
                self.assertEqual(arquivo_extraido.read(), "conteudo longo")
            self.assertFalse((destino / "_caminhos_longos").exists())
            self.assertFalse((destino / "_nemesis_mapas").exists())
            self.assertIsNone(relatorio["mapa"])
            inventario = auditar_pasta_extraida(destino)
            self.assertEqual(inventario["extraidos"], 1)
            self.assertEqual(inventario["bytes_extraidos"], len("conteudo longo"))
            self.assertEqual(inventario["erros_leitura"], [])

    def test_salva_mapa_tecnico_fora_da_pasta_extraida(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo = raiz / "nomes.zip"
            destino = raiz / "saida"
            relatorio = {}
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("/Drive/arquivo.txt", "conteudo")

            extrair_zip_seguro(arquivo, destino, relatorio)

            mapa = Path(relatorio["mapa"])
            self.assertTrue(mapa.is_file())
            self.assertTrue(os.path.samefile(mapa.parent, raiz))
            self.assertFalse((destino / "_nemesis_mapas").exists())
            self.assertFalse((raiz / "_nemesis_mapas").exists())

    def test_nao_substitui_arquivo_existente_com_conteudo_diferente(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            destino = raiz / "saida"
            (destino / "Fotos").mkdir(parents=True)
            existente = destino / "Fotos" / "imagem.heic"
            existente.write_bytes(b"original")
            arquivo = raiz / "novo.zip"
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("Fotos/imagem.heic", b"diferente")

            with self.assertRaisesRegex(FileExistsError, "Nenhum arquivo será substituído"):
                extrair_zip_seguro(arquivo, destino)

            self.assertEqual(existente.read_bytes(), b"original")

    def test_reutiliza_arquivo_existente_somente_quando_for_identico(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            destino = raiz / "saida"
            (destino / "Fotos").mkdir(parents=True)
            existente = destino / "Fotos" / "imagem.heic"
            existente.write_bytes(b"mesmo-conteudo")
            arquivo = raiz / "novo.zip"
            relatorio = {}
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("Fotos/imagem.heic", b"mesmo-conteudo")

            quantidade = extrair_zip_seguro(arquivo, destino, relatorio)

            self.assertEqual(quantidade, 0)
            self.assertEqual(relatorio["arquivos_reutilizados_identicos"], 1)
            self.assertEqual(relatorio["bytes_reutilizados_identicos"], len(b"mesmo-conteudo"))
            self.assertEqual(relatorio["arquivos_manifestados_zip"], 1)
            self.assertEqual(relatorio["bytes_manifestados_zip"], len(b"mesmo-conteudo"))

    def test_detecta_relatorio_de_falha_gerado_pela_apple(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo = raiz / "aviso-apple.zip"
            destino = raiz / "saida"
            relatorio = {}
            texto = """The following files failed when creating the iCloud backup data archive:

[N_EXEMPLO_1]
[N_EXEMPLO_2]
"""
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("reports/failed_files.txt", texto)

            extrair_zip_seguro(arquivo, destino, relatorio)

            self.assertEqual(len(relatorio["avisos_origem"]), 1)
            aviso = relatorio["avisos_origem"][0]
            self.assertEqual(aviso["origem"], "Apple")
            self.assertEqual(aviso["codigo"], "APPLE_ICLOUD_ARCHIVE_CREATION_FAILED")
            self.assertEqual(aviso["quantidade_itens"], 2)
            self.assertEqual(aviso["itens"], ["N_EXEMPLO_1", "N_EXEMPLO_2"])

    def test_audita_pasta_existente_sem_precisar_baixar_novamente(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            (raiz / "dados.bin").write_bytes(b"123456")
            (raiz / "failed.txt").write_text(
                "The following files failed when creating the iCloud backup data archive:\n"
                "[N_FALHOU]\n",
                encoding="utf-8",
            )

            relatorio = auditar_pasta_extraida(raiz)

            self.assertEqual(relatorio["extraidos"], 2)
            self.assertGreater(relatorio["bytes_extraidos"], 6)
            self.assertEqual(relatorio["avisos_origem"][0]["itens"], ["N_FALHOU"])
            self.assertTrue(relatorio["auditoria_pos_execucao"])

    def test_bloqueia_zip_com_path_traversal(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo = raiz / "malicioso.zip"
            destino = raiz / "saida"
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr("../fora.txt", "nao deve sair")

            with self.assertRaises(ValueError):
                extrair_zip_seguro(arquivo, destino)

            self.assertFalse((raiz / "fora.txt").exists())

    def test_bloqueia_traversal_com_barras_do_windows(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo = raiz / "malicioso-windows.zip"
            with zipfile.ZipFile(arquivo, "w") as zipf:
                zipf.writestr(r"..\fora.txt", "nao deve sair")

            with self.assertRaises(ValueError):
                extrair_zip_seguro(arquivo, raiz / "saida")


if __name__ == "__main__":
    unittest.main()
