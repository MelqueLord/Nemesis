from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest

from nemesis_job import (
    ARQUIVO_ESTADO,
    carregar_estado,
    criar_diretorio_execucao,
    localizar_estado_pendente,
    localizar_estado_mais_recente,
    salvar_estado_atomico,
)


class TestPersistenciaExecucao(unittest.TestCase):
    def test_salva_e_carrega_estado_atomicamente(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / ARQUIVO_ESTADO
            estado = {"schema_version": 1, "status": "parcial", "texto": "ação"}
            salvar_estado_atomico(caminho, estado)
            self.assertEqual(carregar_estado(caminho), estado)
            self.assertFalse(caminho.with_name(f"{caminho.name}.tmp").exists())

    def test_localiza_execucao_pendente_mais_recente(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            antiga = raiz / "execucoes" / "antiga" / ARQUIVO_ESTADO
            nova = raiz / "execucoes" / "nova" / ARQUIVO_ESTADO
            concluida = raiz / "execucoes" / "concluida" / ARQUIVO_ESTADO
            salvar_estado_atomico(antiga, {"schema_version": 1, "status": "parcial"})
            salvar_estado_atomico(nova, {"schema_version": 1, "status": "em_andamento"})
            salvar_estado_atomico(concluida, {"schema_version": 1, "status": "concluido"})
            os.utime(antiga, (1, 1))
            os.utime(nova, (2, 2))
            os.utime(concluida, (3, 3))
            self.assertEqual(localizar_estado_pendente(raiz), nova)

    def test_cria_pastas_distintas_para_execucoes_simultaneas(self):
        with tempfile.TemporaryDirectory() as pasta:
            instante = datetime(2026, 10, 21, 12, 30, 45)
            primeira = criar_diretorio_execucao(pasta, "https://example.test/a", instante)
            segunda = criar_diretorio_execucao(pasta, "https://example.test/a", instante)
            self.assertTrue(primeira.is_dir())
            self.assertTrue(segunda.is_dir())
            self.assertNotEqual(primeira, segunda)
            self.assertTrue(primeira.name.startswith("20261021-123045-"))

    def test_localiza_execucao_mais_recente_mesmo_concluida(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            pendente = raiz / "execucoes" / "pendente" / ARQUIVO_ESTADO
            concluida = raiz / "execucoes" / "concluida" / ARQUIVO_ESTADO
            salvar_estado_atomico(pendente, {"schema_version": 1, "status": "parcial"})
            salvar_estado_atomico(concluida, {"schema_version": 1, "status": "concluido"})
            os.utime(pendente, (1, 1))
            os.utime(concluida, (2, 2))
            self.assertEqual(localizar_estado_mais_recente(raiz), concluida)

    def test_rejeita_schema_incompativel(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / ARQUIVO_ESTADO
            caminho.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
            with self.assertRaises(ValueError):
                carregar_estado(caminho)


if __name__ == "__main__":
    unittest.main()
