# Nêmesis 1.6.0

O arquivo oficial desta versão é `nemesis.py`. Os arquivos `XX.py`, `atual.py` e
`mainCopy.py` são cópias históricas e não devem ser usados para gerar o executável.

O executável validado está em `release\Nemesis-1.6.0-Windows-Server-x64.exe`. As pastas `dist*` e
`build*` são artefatos intermediários e não identificam a versão oficial.

## Pré-requisitos

- Windows 10 ou 11, ou Windows Server 2019/2022/2025 com Desktop Experience;
- Python 3.13 recomendado para desenvolvimento e geração do executável;
- GPG 2.5.24 disponível em `gpg\bin` para desenvolvimento e geração do executável.

O executável oficial já incorpora o GPG e não exige uma instalação separada no
computador de destino. O arquivo `nemesis.spec` inclui automaticamente a pasta
`gpg` quando existe `gpg\bin\gpg.exe`.

## Preparação do ambiente

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

O diretório antigo `venv` veio de outro computador e não deve ser reutilizado.

## Validação

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe nemesis.py
```

## Retomada, logs e uso de disco

Cada execução é isolada em `<analista>\execucoes\<data-hora-id>`. Nessa pasta,
`estado.json` guarda o ponto de retomada e `nemesis.log` registra todas as etapas.
A senha GPG nunca é gravada. Depois de uma interrupção ou reinício, informe o
mesmo analista e a senha e clique em **Retomar execução**.

Os produtos são baixados, descriptografados e extraídos um por vez. Arquivos
GPG e ZIP transitórios são removidos somente após a respectiva etapa terminar;
arquivos necessários para diagnóstico ou retomada são preservados.

Cada produto é extraído em uma pasta própria com o título informado pelo
manifesto da Apple, como `iCloud Reminders`, `Safari Browsing History` e
`Sign in with Apple`. O nome original do pacote (por exemplo,
`2354676-ICLOUDREMINDERS-41489874.zip.gpg`) é mantido durante download e
descriptografia. Dentro da pasta do produto, a estrutura e todos os nomes do ZIP
continuam exatamente como vieram da Apple. Pacotes em várias partes do mesmo
produto são reunidos na mesma pasta e passam pelas mesmas verificações de
colisão e integridade.

ZIPs exportados pela Apple podem conter caminhos iniciados por `/`, caracteres
proibidos no Windows ou estruturas maiores que o limite tradicional de caminho.
Caminhos válidos preservam nomes e estrutura; caminhos longos usam o suporte
nativo do Windows. A raiz externa `/` é removida com registro de rastreabilidade.

Antes de extrair, todos os destinos são conferidos. Um arquivo existente só é
reutilizado quando tamanho e SHA-256 são idênticos; conteúdo diferente nunca é
substituído. Quando um ZIP de produto contém nomes incompatíveis ou conflitos,
suas entradas são recuperadas em `_nemesis_recuperados/<sha256-do-zip>`, com
nomes únicos. O ZIP original permanece em `zips` e um mapa JSON ao lado dele
registra índice, caminho original, caminho recuperado, tamanho e SHA-256 de
cada entrada. O relatório apresenta os caminhos e hashes do ZIP e do mapa.
Caminhos inseguros, links simbólicos e outros erros continuam bloqueados. A
recuperação exige espaço adicional e uma interrupção pode exigir retomada.
O relatório registra, por
produto, bytes recebidos, bytes declarados dentro dos ZIPs, bytes efetivamente
gravados, arquivos idênticos reutilizados e o inventário final da pasta.

Antes do primeiro download de produto, o Nêmesis consulta todos os links da
Apple por `HEAD` ou por uma faixa de um byte e registra o tamanho remoto exato
por produto e por conta. Se algum tamanho não puder ser obtido, nenhum download
de produto começa. Durante a transferência, o tamanho e o ETag (quando
disponível) são comparados novamente. A conclusão integral exige igualdade entre
o inventário prévio, os bytes recebidos, o conteúdo dos ZIPs e o inventário da
pasta final.

Ao terminar, uma janela única apresenta origem, tamanhos, totais por
produto/conta, pendências, avisos da Apple e erros. O botão `OK` fecha o Nêmesis
somente depois que o estado e os relatórios de auditoria foram gravados.

Cada resposta HTTP com `Content-Length` ou `Content-Range` agora é conferida
byte a byte antes de ser marcada como concluída. Interrupções preservam o
arquivo `.part` e retomam do ponto salvo. Respostas sem tamanho HTTP conhecido
geram um aviso visível, e `estado.json` recebe as medições de download e
extração. Ao final, o log apresenta um resumo de integridade e tamanhos.

## Relatório de auditoria

Cada pasta de execução recebe `relatorio_auditoria.json`,
`relatorio_auditoria.txt` e `relatorio_auditoria.sha256.txt`. Os relatórios
classificam ocorrências da origem Apple, transporte HTTP, GPG, ZIP,
disco/servidor e erros internos do Nêmesis. URLs completas, tokens e senha não
são incluídos. O arquivo de hashes permite verificar alterações posteriores
quando sua cópia for preservada separadamente do relatório e do log.

O relatório textual lista os pacotes pendentes e orientações para erros ainda
não resolvidos. A retomada aproveita os ZIPs preservados; a execução permanece
parcial enquanto houver pendências. Falhas declaradas pela Apple são apresentadas
separadamente e podem exigir novo fornecimento pela origem.

Relatórios textuais da Apple que contenham a mensagem `The following files
failed when creating the iCloud backup data archive` são detectados durante a
extração. Nesse caso, a execução termina como concluída com falhas declaradas
pela origem, nunca como sucesso integral.

O botão **Auditar última execução** examina uma extração antiga sem modificar
seu `estado.json` ou `nemesis.log`. Ele serve para localizar avisos da Apple e
gerar os três relatórios de evidência sem repetir o download.

## Gerar o executável

Feche qualquer instância anterior do Nêmesis e execute:

```powershell
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm nemesis.spec
```

O resultado oficial será `dist\nemesis.exe`, com a versão registrada nos
metadados do Windows.
