import json
from pathlib import Path
import tempfile
import unittest

from nemesis_audit import gerar_relatorios_auditoria, montar_resumo_final


class NemesisAuditTests(unittest.TestCase):
    def test_relatorio_indica_pendencias_e_orientacoes_sem_expor_link(self):
        with tempfile.TemporaryDirectory() as pasta:
            url = "https://apple.example/pacote?token=segredo"
            estado = {
                "paths": {"root": pasta}, "status": "parcial",
                "products": {"fotos": [url]}, "link_status": {url: "erro_extracao"},
                "product_metadata": {url: {"nome_produto": "Fotos", "nome_arquivo_original": "fotos.zip.gpg"}},
                "integrity_summary": {"avisos_falha_origem": 1, "integridade_extracao_confirmada": False},
                "incidentes": [
                    {"codigo": "EXTRACTION_PATH_CONFLICT", "etapa": "extracao_zip", "mensagem": "Mesmo nome", "item": "fotos.zip"},
                    {"codigo": "ZIP_PATH_BLOCKED", "etapa": "extracao_zip", "mensagem": "Nome incompatível"},
                    {"codigo": "APPLE_ICLOUD_ARCHIVE_CREATION_FAILED", "etapa": "criacao_pacote_origem"},
                ],
            }
            relatorio = gerar_relatorios_auditoria(estado)
            texto = (Path(pasta) / "relatorio_auditoria.txt").read_text(encoding="utf-8")
            self.assertEqual(relatorio["resultado"]["classificacao"], "EXECUCAO_INCOMPLETA")
            self.assertIn("INCOMPLETO OU COM ERRO", montar_resumo_final(estado, False))
            self.assertIn("fotos.zip.gpg", texto)
            self.assertIn("fotos.zip", texto)
            self.assertIn("locais separados", texto)
            self.assertIn("sistema compatível", texto)
            self.assertIn("falhas na criação", montar_resumo_final(estado, False))
            self.assertNotIn("token=segredo", texto)
            self.assertNotIn(url, json.dumps(relatorio))

    def test_resumo_final_mostra_origem_tamanhos_e_resultado(self):
        estado = {
            "paths": {"root": "C:/coleta/teste"},
            "link_status": {"item": "concluido"},
            "remote_inventory_summary": {
                "arquivos": 1,
                "arquivos_com_tamanho": 1,
                "bytes_esperados": 100,
                "completo": True,
                "por_produto": {"iCloudPhotoslinks": {"bytes_esperados": 100}},
            },
            "integrity_summary": {
                "bytes_recebidos": 100,
                "bytes_manifestados_zip": 200,
                "bytes_extraidos": 200,
                "avisos_falha_origem": 0,
                "http_totalmente_verificado": True,
                "integridade_extracao_confirmada": True,
                "inventario_final": {"erros_leitura": []},
                "por_produto": {
                    "iCloudPhotoslinks": {
                        "bytes_manifestados_zip": 200,
                        "arquivos_manifestados_zip": 2,
                    }
                },
            },
        }

        resumo = montar_resumo_final(estado, True)

        self.assertIn("CONCLUÍDO E CONFERIDO", resumo)
        self.assertIn("Origem: Apple", resumo)
        self.assertIn("iCloudPhotoslinks", resumo)
        self.assertIn("Clique em OK", resumo)

    def test_relatorio_classifica_falha_da_origem_sem_expor_url(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            log = raiz / "nemesis.log"
            log.write_text("registro de teste\n", encoding="utf-8")
            url = "https://apple.example/arquivo?token=segredo"
            estado = {
                "app_version": "1.3.0",
                "analyst": "Analista",
                "created_at": "2026-10-27T10:00:00",
                "updated_at": "2026-10-27T10:10:00",
                "status": "concluido",
                "fase": "concluido",
                "paths": {"root": str(raiz)},
                "products": {"iCloudBackuplinks": [url]},
                "link_status": {url: "concluido"},
                "download_checks": {
                    url: {
                        "bytes_esperados": 10,
                        "bytes_recebidos": 10,
                        "tamanho_http_verificado": True,
                        "sha256_arquivo_recebido": "ABC",
                    }
                },
                "extraction_checks": {
                    url: {
                        "extraidos": 1,
                        "bytes_extraidos": 20,
                        "renomeados": 0,
                        "avisos_origem": [
                            {
                                "origem": "Apple",
                                "codigo": "APPLE_ICLOUD_ARCHIVE_CREATION_FAILED",
                                "arquivo_relatorio": "failed.txt",
                                "mensagem": "Falha declarada pela Apple.",
                                "quantidade_itens": 1,
                                "itens": ["N_EXEMPLO"],
                            }
                        ],
                    }
                },
                "integrity_summary": {
                    "downloads_registrados": 1,
                    "downloads_http_verificados": 1,
                    "downloads_sem_tamanho_http": 0,
                    "bytes_http_esperados_conhecidos": 10,
                    "bytes_recebidos": 10,
                    "bytes_extraidos": 20,
                    "http_totalmente_verificado": True,
                },
                "incidentes": [],
            }

            relatorio = gerar_relatorios_auditoria(estado, log)

            self.assertEqual(
                relatorio["resultado"]["classificacao"],
                "CONCLUIDO_COM_FALHAS_DECLARADAS_PELA_ORIGEM",
            )
            conteudo = (raiz / "relatorio_auditoria.json").read_text(encoding="utf-8")
            self.assertNotIn(url, conteudo)
            self.assertNotIn("token=segredo", conteudo)
            self.assertIn("APPLE_ICLOUD_ARCHIVE_CREATION_FAILED", conteudo)
            self.assertTrue((raiz / "relatorio_auditoria.txt").is_file())
            hashes = (raiz / "relatorio_auditoria.sha256.txt").read_text(encoding="utf-8")
            self.assertIn("relatorio_auditoria.json", hashes)
            self.assertIn("nemesis.log", hashes)


if __name__ == "__main__":
    unittest.main()
