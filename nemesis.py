import sys
import os
import requests
import zipfile
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from PIL import Image, ImageTk
from ttkbootstrap import Style
from ttkbootstrap.widgets import Floodgauge
import subprocess
import threading
import time
import queue
import copy
import hashlib
import re
import shutil
import traceback
from collections import defaultdict
import glob
from pathlib import Path
from datetime import datetime

from nemesis_audit import calcular_sha256, gerar_relatorios_auditoria, montar_resumo_final

from nemesis_core import (
    APP_VERSION,
    NomeZipIncompativel,
    auditar_pasta_extraida,
    calcular_limite_descriptografia,
    calcular_tamanho_janela,
    extrair_links_html,
    extrair_links_html_detalhados,
    extrair_zip_seguro,
    recuperar_zip_com_mapa,
    formatar_tamanho_bytes,
    localizar_gpg,
    nome_download_estavel,
    nome_arquivo_seguro,
    nome_arquivo_original_url,
    nome_pasta_produto,
    pasta_base_execucao,
    pasta_saida_segura,
    parametros_retomada_http,
    resource_path,
    resumir_etapas_links,
    tamanho_total_resposta_http,
    url_segura_para_log,
    validar_tamanho_download,
    validar_url_https,
)
from nemesis_job import (
    ARQUIVO_ESTADO,
    ARQUIVO_LOG,
    carregar_estado,
    criar_diretorio_execucao,
    localizar_estado_mais_recente,
    localizar_estado_pendente,
    salvar_estado_atomico,
)


# Configuração de caminhos e variáveis globais
caminho_logo = resource_path("logo2.png")
historico_downloads = set()
links_armazenados = defaultdict(list)
status_downloads = {}
status_links = {}
arquivos_html_processados = set()
contas_processadas = {}
fila_interface = queue.Queue()
estado_execucao = {}
caminho_estado_atual = None
caminho_log_atual = None
execucao_em_andamento = False
lock_estado = threading.RLock()
lock_log = threading.Lock()


def executar_na_interface(funcao, *args, **kwargs):
    """Executa imediatamente na thread principal ou agenda a chamada com segurança."""
    if threading.current_thread() is threading.main_thread():
        return funcao(*args, **kwargs)
    fila_interface.put((funcao, args, kwargs))
    return None


def processar_fila_interface():
    """Processa chamadas de interface produzidas pelas tarefas em segundo plano."""
    while True:
        try:
            funcao, args, kwargs = fila_interface.get_nowait()
        except queue.Empty:
            break
        try:
            funcao(*args, **kwargs)
        except Exception as erro:
            print(f"Erro ao atualizar a interface: {erro}", file=sys.stderr)
    app.after(50, processar_fila_interface)

def limpar_arquivos_temporarios(pasta_temp, pasta_zips):
    # Limpa arquivos GPG após a conclusão integral do processamento.
    for root, _, files in os.walk(pasta_temp):
        for file in files:
            if file.endswith('.gpg'):
                try:
                    os.remove(os.path.join(root, file))
                    log_message(f"Removido arquivo temporário concluído: {file}", "info")
                except Exception as e:
                    log_message(f"Erro ao remover {file}: {str(e)}", "error")
    
    # Limpa arquivos ZIP após a conclusão integral do processamento.
    for root, _, files in os.walk(pasta_zips):
        for file in files:
            if file.endswith('.zip'):
                try:
                    os.remove(os.path.join(root, file))
                    log_message(f"Removido arquivo ZIP concluído: {file}", "info")
                except Exception as e:
                    log_message(f"Erro ao remover {file}: {str(e)}", "error")

# Configuração da interface gráfica. Criamos a raiz primeiro para carregar o
# catálogo Tcl antes que o ttkbootstrap registre suas traduções.
app = tk.Tk()
localizacao_tcl_disponivel = False
try:
    biblioteca_tcl = Path(app.tk.eval("info library"))
    for pasta_modulos in (biblioteca_tcl / "tcl8", biblioteca_tcl.parent / "tcl8"):
        if pasta_modulos.is_dir():
            app.tk.call("::tcl::tm::path", "add", str(pasta_modulos))
    app.tk.call("package", "require", "msgcat")
    if not app.tk.call("info", "commands", "::msgcat::mcmset"):
        raise tk.TclError("comando ::msgcat::mcmset indisponível")
    localizacao_tcl_disponivel = True
except tk.TclError as erro:
    print(f"Aviso ao carregar traduções Tcl: {erro}", file=sys.stderr)

# Alguns ambientes empacotados não expõem o catálogo msgcat completo. O Nêmesis
# não usa diálogos traduzidos do ttkbootstrap, então desativamos somente essa
# inicialização opcional e preservamos todos os demais recursos visuais.
if not localizacao_tcl_disponivel:
    from ttkbootstrap import localization as ttk_localization

    ttk_localization.initialize_localities = lambda: None

style = Style(theme="darkly")
app.title("Nêmesis - Gerenciador de Downloads")
largura_tela = app.winfo_screenwidth()
altura_tela = app.winfo_screenheight()
largura_inicial, altura_inicial = calcular_tamanho_janela(largura_tela, altura_tela)
posicao_x = max(0, (largura_tela - largura_inicial) // 2)
posicao_y = max(0, (altura_tela - altura_inicial) // 2)
app.geometry(f"{largura_inicial}x{altura_inicial}+{posicao_x}+{posicao_y}")
app.minsize(min(640, largura_inicial), min(480, altura_inicial))
app.resizable(True, True)
app.grid_rowconfigure(0, weight=1)
app.grid_rowconfigure(1, weight=0)
app.grid_columnconfigure(0, weight=1)

# Estilos personalizados
style.configure("TLabel", font=("Helvetica", 10))
style.configure("TButton", font=("Helvetica", 10, "bold"))
style.configure("Title.TLabel", font=("Helvetica", 14, "bold"))
style.configure("Status.TLabel", font=("Helvetica", 11))
style.configure("Success.TLabel", foreground="green", font=("Helvetica", 11))
style.configure("Error.TLabel", foreground="red", font=("Helvetica", 11))
style.configure("Progress.TLabel", font=("Helvetica", 10))

# Frame principal
main_frame = ttk.Frame(app, padding=10)
main_frame.grid(row=0, column=0, sticky="nsew")

# Cabeçalho com logomarca
header_frame = ttk.Frame(main_frame)
header_frame.pack(fill=tk.X, pady=(0, 10))

try:
    logo_img = Image.open(caminho_logo)
    logo_img = logo_img.resize((120, 120), Image.LANCZOS)
    logo = ImageTk.PhotoImage(logo_img)
    logo_label = ttk.Label(header_frame, image=logo)
    logo_label.image = logo
    logo_label.pack(side=tk.LEFT, padx=10)

    title_frame = ttk.Frame(header_frame)
    title_frame.pack(side=tk.LEFT, fill=tk.Y, expand=True)
    
    ttk.Label(title_frame, text=f"NÊMESIS {APP_VERSION}", style="Title.TLabel").pack(anchor="w")
    ttk.Label(title_frame, text="Gerenciador de Downloads Seguros", style="TLabel").pack(anchor="w")
    
except Exception as e:
    print(f"Erro ao carregar logo: {e}")

# Frame de entrada de dados
input_frame = ttk.LabelFrame(main_frame, text="Informações do Download", padding=15)
input_frame.pack(fill=tk.X, pady=5)
input_frame.columnconfigure(1, weight=1)

# Campos de entrada com validação
def validate_input():
    all_filled = all([
        entrada_analista.get().strip(),
        entrada_url.get().strip(),
        entrada_senha.get().strip()
    ])
    btn_processar.state(["!disabled" if all_filled else "disabled"])

ttk.Label(input_frame, text="Nome do Analista:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
entrada_analista = ttk.Entry(input_frame, width=50)
entrada_analista.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
entrada_analista.bind("<KeyRelease>", lambda e: validate_input())

ttk.Label(input_frame, text="URL do Arquivo:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
entrada_url = ttk.Entry(input_frame, width=50)
entrada_url.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
entrada_url.bind("<KeyRelease>", lambda e: validate_input())

ttk.Label(input_frame, text="Senha GPG:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
entrada_senha = ttk.Entry(input_frame, width=50, show="•")
entrada_senha.grid(row=2, column=1, sticky="ew", padx=5, pady=5)
entrada_senha.bind("<KeyRelease>", lambda e: validate_input())

# Frame de status e progresso
status_frame = ttk.LabelFrame(main_frame, text="Estrutura de Downloads", padding=15)
status_frame.pack(fill=tk.BOTH, expand=True, pady=10)

# Barra de progresso principal
progress_container = ttk.Frame(status_frame)
progress_container.pack(fill=tk.X, pady=5)

lbl_progress_status = ttk.Label(progress_container, text="Aguardando início...", style="Status.TLabel")
lbl_progress_status.pack(anchor="w")

progress_bar = Floodgauge(
    progress_container, 
    bootstyle="success", 
    maximum=100, 
    mode="determinate",
    text="0%",
    font=("Helvetica", 10)
)
progress_bar.pack(fill=tk.X, pady=5)

# Frame de detalhes do progresso
details_frame = ttk.Frame(status_frame)
details_frame.pack(fill=tk.BOTH, expand=True)

# Árvore de visualização com barras de rolagem
tree_container = ttk.Frame(details_frame)
tree_container.pack(fill=tk.BOTH, expand=True)

tree_scroll_y = ttk.Scrollbar(tree_container)
tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

tree_scroll_x = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL)
tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

downloads_tree = ttk.Treeview(
    tree_container,
    columns=("item", "status", "detalhes"),
    show="tree headings",
    height=8,
    selectmode="extended",
    yscrollcommand=tree_scroll_y.set,
    xscrollcommand=tree_scroll_x.set
)

tree_scroll_y.config(command=downloads_tree.yview)
tree_scroll_x.config(command=downloads_tree.xview)

downloads_tree.pack(fill=tk.BOTH, expand=True)

# Configurar colunas
downloads_tree.heading("#0", text="Estrutura", anchor=tk.W)
downloads_tree.heading("item", text="Item", anchor=tk.W)
downloads_tree.heading("status", text="Status", anchor=tk.W)
downloads_tree.heading("detalhes", text="Detalhes", anchor=tk.W)

downloads_tree.column("#0", width=300, stretch=tk.YES)
downloads_tree.column("item", width=150, stretch=tk.YES)
downloads_tree.column("status", width=150, stretch=tk.YES)
downloads_tree.column("detalhes", width=200, stretch=tk.YES)

# Tags para estilização
downloads_tree.tag_configure("conta", font=('Helvetica', 10, 'bold'))
downloads_tree.tag_configure("produto", font=('Helvetica', 9))
downloads_tree.tag_configure("success", foreground="green")
downloads_tree.tag_configure("error", foreground="red")
downloads_tree.tag_configure("warning", foreground="orange")
downloads_tree.tag_configure("pending", foreground="gray")

# Frame de botões
button_frame = ttk.Frame(app, padding=(10, 0, 10, 10))
button_frame.grid(row=1, column=0, sticky="ew")

btn_processar = ttk.Button(
    button_frame, 
    text="Iniciar Download", 
    command=lambda: iniciar_processamento(),
    style="success.TButton",
    state="disabled"
)
btn_processar.pack(side=tk.LEFT, padx=5, ipadx=20)
app.bind("<Control-Return>", lambda _evento: btn_processar.invoke())

btn_retomar = ttk.Button(
    button_frame, 
    text="Retomar execução", 
    command=lambda: iniciar_retomada(),
    style="info.TButton"
)
btn_retomar.pack(side=tk.LEFT, padx=5, ipadx=20)

btn_auditar = ttk.Button(
    button_frame,
    text="Auditar última execução",
    command=lambda: iniciar_auditoria_existente(),
    style="warning.TButton",
)
btn_auditar.pack(side=tk.LEFT, padx=5, ipadx=10)

# Console de logs
log_frame = ttk.LabelFrame(main_frame, text="Log de Operações", padding=10)
log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

log_console = scrolledtext.ScrolledText(
    log_frame, 
    width=80, 
    height=10, 
    wrap=tk.WORD,
    font=("Consolas", 9),
    bg="#2b2b2b",
    fg="white",
    insertbackground="white"
)
log_console.pack(fill=tk.BOTH, expand=True)



def limpar_historico():
    """Limpa todo o histórico de downloads e contas processadas"""
    global historico_downloads, links_armazenados, status_downloads, status_links
    global arquivos_html_processados, contas_processadas
    
    # Limpar estruturas de dados
    historico_downloads.clear()
    links_armazenados.clear()
    status_downloads.clear()
    status_links.clear()
    arquivos_html_processados.clear()
    contas_processadas.clear()
    
    executar_na_interface(limpar_interface)
    
    log_message("Histórico anterior limpo com sucesso.", "success")


def limpar_interface():
    downloads_tree.delete(*downloads_tree.get_children())
    log_console.delete("1.0", tk.END)
    progress_bar["value"] = 0
    progress_bar.configure(text="0%")
    lbl_progress_status.config(text="Aguardando início...")


# Funções auxiliares para UI
def _gravar_log(message, tipo):
    if not caminho_log_atual:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rotulo = {"error": "ERRO", "warning": "AVISO", "success": "SUCESSO"}.get(tipo, "INFO")
    try:
        with lock_log:
            Path(caminho_log_atual).parent.mkdir(parents=True, exist_ok=True)
            with open(caminho_log_atual, "a", encoding="utf-8", newline="\n") as arquivo:
                arquivo.write(f"[{timestamp}] {rotulo}: {message}\n")
    except OSError as erro:
        print(f"Erro ao gravar log: {erro}", file=sys.stderr)


def log_message(message, type="info", persistir=True):
    if persistir:
        _gravar_log(message, type)
    if threading.current_thread() is not threading.main_thread():
        executar_na_interface(log_message, message, type, False)
        return

    timestamp = time.strftime("%H:%M:%S")

    if type == "error":
        log_console.insert(tk.END, f"[{timestamp}] ERRO: {message}\n", "error")
    elif type == "warning":
        log_console.insert(tk.END, f"[{timestamp}] AVISO: {message}\n", "warning")
    elif type == "success":
        log_console.insert(tk.END, f"[{timestamp}] SUCESSO: {message}\n", "success")
    else:
        log_console.insert(tk.END, f"[{timestamp}] INFO: {message}\n")
    
    log_console.tag_config("error", foreground="#ff6b6b")
    log_console.tag_config("warning", foreground="#ffd43b")
    log_console.tag_config("success", foreground="#51cf66")
    log_console.see(tk.END)

def update_progress(percent, message=None):
    if threading.current_thread() is not threading.main_thread():
        executar_na_interface(update_progress, percent, message)
        return

    percent = max(0, min(100, int(percent)))
    progress_bar["value"] = percent
    progress_bar.configure(text=f"{percent}%")
    if message:
        lbl_progress_status.config(text=message)

def show_error(message):
    if threading.current_thread() is not threading.main_thread():
        executar_na_interface(show_error, message)
        return
    messagebox.showerror("Erro", message)
    log_message(message, "error")

def show_warning(message):
    if threading.current_thread() is not threading.main_thread():
        executar_na_interface(show_warning, message)
        return
    messagebox.showwarning("Aviso", message)
    log_message(message, "warning")

def show_info(message):
    if threading.current_thread() is not threading.main_thread():
        executar_na_interface(show_info, message)
        return
    messagebox.showinfo("Informação", message)
    log_message(message)


def mostrar_resumo_final_e_fechar(mensagem, sucesso_integral):
    """Exibe uma única confirmação final e fecha o aplicativo após o OK."""
    if threading.current_thread() is not threading.main_thread():
        executar_na_interface(mostrar_resumo_final_e_fechar, mensagem, sucesso_integral)
        return
    titulo = "Coleta concluída e conferida" if sucesso_integral else "Coleta finalizada com ressalvas"
    janela = tk.Toplevel(app)
    janela.title(titulo)
    janela.transient(app)
    janela.grab_set()
    largura = min(760, max(560, app.winfo_screenwidth() - 80))
    altura = min(560, max(420, app.winfo_screenheight() - 100))
    pos_x = max(0, (app.winfo_screenwidth() - largura) // 2)
    pos_y = max(0, (app.winfo_screenheight() - altura) // 2)
    janela.geometry(f"{largura}x{altura}+{pos_x}+{pos_y}")
    janela.minsize(min(520, largura), min(380, altura))

    cor = "#1f8b4c" if sucesso_integral else "#b36b00"
    ttk.Label(
        janela,
        text=titulo,
        font=("Helvetica", 14, "bold"),
        foreground=cor,
    ).pack(fill=tk.X, padx=18, pady=(16, 8))
    texto = scrolledtext.ScrolledText(janela, wrap=tk.WORD, font=("Consolas", 9))
    texto.pack(fill=tk.BOTH, expand=True, padx=18, pady=8)
    texto.insert("1.0", mensagem)
    texto.configure(state=tk.DISABLED)

    def fechar_resumo():
        try:
            janela.grab_release()
        except tk.TclError:
            pass
        app.destroy()

    ttk.Button(janela, text="OK", command=fechar_resumo, width=18).pack(pady=(4, 16))
    janela.protocol("WM_DELETE_WINDOW", fechar_resumo)
    janela.lift()
    janela.focus_force()

# Funções principais do aplicativo
def atualizar_lista(
    contas_snapshot=None,
    status_snapshot=None,
    historico_snapshot=None,
    status_links_snapshot=None,
):
    if threading.current_thread() is not threading.main_thread():
        executar_na_interface(
            atualizar_lista,
            copy.deepcopy(contas_processadas),
            dict(status_downloads),
            set(historico_downloads),
            dict(status_links),
        )
        return

    contas_exibicao = contas_processadas if contas_snapshot is None else contas_snapshot
    status_exibicao = status_downloads if status_snapshot is None else status_snapshot
    historico_exibicao = historico_downloads if historico_snapshot is None else historico_snapshot
    etapas_exibicao = status_links if status_links_snapshot is None else status_links_snapshot

    downloads_tree.delete(*downloads_tree.get_children())
    erro_existe = False
    
    # Adiciona o item principal (analista)
    analista = entrada_analista.get().strip() or "Nêmesis"
    main_item = downloads_tree.insert("", "end", text=f"📁 {analista}", values=("", "", ""), tags=("conta",))
    
    # Adiciona cada conta como filho do item principal
    for conta, dados in contas_exibicao.items():
        links_conta = [link for links in dados["produtos"].values() for link in links]
        etapas_conta = [etapas_exibicao.get(link, "pendente") for link in links_conta]
        if links_conta and all(etapa == "concluido" for etapa in etapas_conta):
            conta_status = "✅ Processamento completo"
        elif any(etapa.startswith("erro_") for etapa in etapas_conta):
            conta_status = "⚠️ Processamento parcial"
        elif links_conta and all(etapa not in ("pendente", "erro_download") for etapa in etapas_conta):
            conta_status = "🔐 Processando"
        elif any(etapa != "pendente" for etapa in etapas_conta):
            conta_status = "⬇️ Download em andamento"
        else:
            conta_status = dados["status"]
        tamanho_html = dados.get("tamanho", "N/A")  # Obter o tamanho do HTML
        
        status_conta_normalizado = conta_status.lower()
        if "erro" in status_conta_normalizado or "❌" in conta_status:
            conta_tag = "error"
            erro_existe = True
        elif "completo" in status_conta_normalizado or "✅" in conta_status:
            conta_tag = "success"
        elif "parcial" in status_conta_normalizado or "incompleta" in status_conta_normalizado or "⚠️" in conta_status:
            conta_tag = "warning"
        else:
            conta_tag = "pending"
        
        conta_item = downloads_tree.insert(
            main_item, "end", 
            text=f"📄 {conta}", 
            values=("Conta", conta_status, f"{len(dados['produtos'])} produtos | {tamanho_html}"),
            tags=("conta", conta_tag)
        )
        
        # Adiciona os produtos de cada conta
        for id_tag, links in dados["produtos"].items():
            produto_status = resumir_etapas_links(links, etapas_exibicao)
            
            if "Erro" in produto_status or "❌" in produto_status:
                prod_tag = "error"
                erro_existe = True
            elif "✅" in produto_status or "Concluído" in produto_status:
                prod_tag = "success"
            else:
                prod_tag = "pending"
            
            # Conta quantos arquivos foram baixados para este produto
            baixados = sum(1 for link in links if link in historico_exibicao)
            total = len(links)
            
            inventario_remoto = estado_execucao.get("remote_inventory", {})
            links_unicos_produto = set(links)
            tamanhos_produto = [
                int(inventario_remoto[link].get("bytes_esperados") or 0)
                for link in links_unicos_produto
                if isinstance(inventario_remoto.get(link), dict)
            ]
            detalhe_tamanho = ""
            if len(tamanhos_produto) == len(links_unicos_produto) and links_unicos_produto:
                detalhe_tamanho = (
                    f" | {formatar_tamanho_bytes(sum(tamanhos_produto))} previstos"
                )
            downloads_tree.insert(
                conta_item, "end",
                text=f"🆔 {id_tag}",
                values=("Produto", produto_status, f"{baixados}/{total} arquivos{detalhe_tamanho}"),
                tags=("produto", prod_tag)
            )
    
    # Expande todos os itens por padrão
    downloads_tree.item(main_item, open=True)
    for child in downloads_tree.get_children(main_item):
        downloads_tree.item(child, open=True)
    
    # O botão permanece acessível para retomar uma execução salva por uma sessão
    # anterior, inclusive depois de reiniciar o Windows ou reconectar via RDP.


def registrar_erro_conta(id_tag, campo, valor):
    """Registra o erro na conta que realmente contém o produto informado."""
    for dados_conta in contas_processadas.values():
        if id_tag in dados_conta.get("produtos", {}):
            dados_conta.setdefault(campo, []).append(valor)
            return
    log_message(f"Não foi possível associar o produto {id_tag} a uma conta.", "warning")


def registrar_incidente(categoria, etapa, codigo, mensagem, item=None, tentativa=None):
    """Registra uma ocorrência estruturada sem armazenar URL ou senha."""
    if not estado_execucao:
        return
    incidente = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "categoria": categoria,
        "etapa": etapa,
        "codigo": codigo,
        "mensagem": mensagem,
    }
    if item:
        incidente["item"] = os.path.basename(str(item))
    if tentativa is not None:
        incidente["tentativa"] = tentativa
    estado_execucao.setdefault("incidentes", []).append(incidente)

def consultar_tamanho_remoto(url, max_tentativas=3):
    """Consulta o tamanho do objeto sem baixar seu corpo completo."""
    if not validar_url_https(url):
        return None
    cabecalhos_base = {"Accept-Encoding": "identity"}
    for tentativa in range(1, max_tentativas + 1):
        for metodo in ("HEAD", "RANGE"):
            try:
                if metodo == "HEAD":
                    response = requests.head(
                        url,
                        allow_redirects=True,
                        timeout=(15, 60),
                        headers=cabecalhos_base,
                    )
                else:
                    response = requests.get(
                        url,
                        stream=True,
                        timeout=(15, 60),
                        headers={**cabecalhos_base, "Range": "bytes=0-0"},
                    )
                with response:
                    if not validar_url_https(response.url):
                        raise requests.exceptions.InvalidURL("Redirecionamento não HTTPS.")
                    if response.status_code >= 400 and response.status_code != 416:
                        continue
                    content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
                    if content_encoding not in ("", "identity"):
                        continue
                    texto_length = response.headers.get("Content-Length", "").strip()
                    content_length = int(texto_length) if texto_length else None
                    tamanho = tamanho_total_resposta_http(
                        response.status_code,
                        content_length,
                        response.headers.get("Content-Range", ""),
                    )
                    if tamanho is None:
                        continue
                    return {
                        "bytes_esperados": tamanho,
                        "metodo": metodo,
                        "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"),
                    }
            except (requests.exceptions.RequestException, OSError, ValueError):
                continue
        if tentativa < max_tentativas:
            time.sleep(min(2 * tentativa, 5))
    return None


def inventariar_tamanhos_remotos(itens):
    """Obtém todos os tamanhos antes de iniciar o primeiro download de produto."""
    inventario = estado_execucao.setdefault("remote_inventory", {})
    falhas = []
    total_itens = len(itens)
    log_message("Consultando os tamanhos declarados pela Apple antes dos downloads...")
    for indice, (id_tag, link) in enumerate(itens, 1):
        produto_metadados = estado_execucao.get("product_metadata", {}).get(link, {})
        origens = estado_execucao.get("link_origins", {}).get(link) or [
            {"conta": "conta_nao_identificada", "produto": id_tag}
        ]
        existente = inventario.get(link)
        etapa_anterior = status_links.get(link)
        download_anterior = estado_execucao.get("download_checks", {}).get(link, {})
        pode_reusar = (etapa_anterior == "concluido" or
                       (etapa_anterior in ("erro_extracao", "pronto_extracao")
                        and isinstance(download_anterior, dict)
                        and download_anterior.get("tamanho_http_verificado")))
        if pode_reusar and isinstance(existente, dict):
            metadados = existente
        else:
            update_progress(
                int((indice - 1) / max(1, total_itens) * 100),
                f"Conferindo tamanho remoto {indice}/{total_itens}",
            )
            metadados = consultar_tamanho_remoto(link)
        if not metadados or metadados.get("bytes_esperados") is None:
            falhas.append({"produto": id_tag, "item": hashlib.sha256(link.encode()).hexdigest()[:12]})
            continue
        inventario[link] = {
            "produto": id_tag,
            "nome_produto": produto_metadados.get("nome_produto") or nome_pasta_produto(id_tag),
            "nome_arquivo_original": produto_metadados.get("nome_arquivo_original")
            or nome_arquivo_original_url(link),
            "origens": origens,
            **metadados,
        }

    por_produto = {}
    por_conta = {}
    for _, dados in inventario.items():
        if not isinstance(dados, dict):
            continue
        produto = str(dados.get("produto") or "produto_nao_identificado")
        resumo = por_produto.setdefault(
            produto,
            {
                "nome_produto": dados.get("nome_produto") or nome_pasta_produto(produto),
                "arquivos": 0,
                "bytes_esperados": 0,
            },
        )
        resumo["arquivos"] += 1
        resumo["bytes_esperados"] += int(dados.get("bytes_esperados") or 0)
        for origem in dados.get("origens") or []:
            conta = str(origem.get("conta") or "conta_nao_identificada")
            produto_conta = str(origem.get("produto") or produto)
            resumo_conta = por_conta.setdefault(conta, {}).setdefault(
                produto_conta,
                {
                    "nome_produto": dados.get("nome_produto") or nome_pasta_produto(produto_conta),
                    "arquivos": 0,
                    "bytes_esperados": 0,
                },
            )
            resumo_conta["arquivos"] += 1
            resumo_conta["bytes_esperados"] += int(dados.get("bytes_esperados") or 0)
    resumo = {
        "arquivos": total_itens,
        "arquivos_com_tamanho": total_itens - len(falhas),
        "bytes_esperados": sum(
            int(inventario[link].get("bytes_esperados") or 0)
            for _, link in itens
            if isinstance(inventario.get(link), dict)
        ),
        "por_produto": por_produto,
        "por_conta": por_conta,
        "falhas": falhas,
        "completo": not falhas and all(link in inventario for _, link in itens),
    }
    estado_execucao["remote_inventory_summary"] = resumo
    for produto, dados in sorted(por_produto.items()):
        log_message(
            f"Tamanho remoto antes do download — {produto}: {dados['arquivos']} arquivo(s), "
            f"{formatar_tamanho_bytes(dados['bytes_esperados'])}."
        )
    for conta, produtos_conta in sorted(por_conta.items()):
        total_conta = sum(item["bytes_esperados"] for item in produtos_conta.values())
        log_message(
            f"Tamanho remoto por conta — {conta}: {formatar_tamanho_bytes(total_conta)}."
        )
    if falhas:
        registrar_incidente(
            "ORIGEM_OU_REDE",
            "inventario_remoto",
            "REMOTE_SIZE_UNAVAILABLE",
            f"A Apple não informou o tamanho de {len(falhas)} arquivo(s). Nenhum download foi iniciado.",
        )
        log_message(
            f"Não foi possível obter o tamanho de {len(falhas)} arquivo(s). "
            "Os downloads não serão iniciados para evitar resultado sem garantia.",
            "error",
        )
    atualizar_lista()
    salvar_estado_atual("parcial" if falhas else "em_andamento", "inventario_remoto")
    return resumo if resumo["completo"] else None


def baixar_arquivo(
    url,
    caminho_destino,
    max_tentativas=5,
    relatorio=None,
    tamanho_aferido=None,
    etag_aferido=None,
):
    """Baixa com retomada e só confirma tamanhos HTTP conhecidos após conferência."""
    detalhes = relatorio if relatorio is not None else {}
    detalhes.clear()
    detalhes["incidentes"] = []
    if not validar_url_https(url):
        log_message("URL recusada: use um endereço HTTPS válido, sem usuário ou senha embutidos.", "error")
        return False

    url_log = url_segura_para_log(url)
    caminho_parcial = f"{caminho_destino}.part"
    tentativas = 0
    while tentativas < max_tentativas:
        tentativas += 1
        try:
            deslocamento = os.path.getsize(caminho_parcial) if os.path.exists(caminho_parcial) else 0
            cabecalhos = {"Accept-Encoding": "identity"}
            if deslocamento:
                cabecalhos["Range"] = f"bytes={deslocamento}-"
            acao = "Retomando" if deslocamento else "Baixando"
            log_message(f"Tentativa {tentativas}/{max_tentativas} - {acao} {url_log}")

            with requests.get(url, stream=True, timeout=(15, 120), headers=cabecalhos) as response:
                if response.status_code == 416 and deslocamento:
                    content_range = response.headers.get("Content-Range", "")
                    correspondencia = re.fullmatch(r"bytes\s+\*/(\d+)", content_range.strip(), re.I)
                    if correspondencia and int(correspondencia.group(1)) == deslocamento:
                        tamanho_final = os.path.getsize(caminho_parcial)
                        if tamanho_aferido is not None and tamanho_final != tamanho_aferido:
                            raise requests.exceptions.InvalidHeader(
                                "O tamanho remoto mudou depois do inventário inicial."
                            )
                        etag_resposta = response.headers.get("ETag")
                        if etag_aferido and etag_resposta and etag_aferido != etag_resposta:
                            raise requests.exceptions.InvalidHeader(
                                "O identificador remoto mudou depois do inventário inicial."
                            )
                        sha256_recebido = calcular_sha256(caminho_parcial)
                        os.replace(caminho_parcial, caminho_destino)
                        detalhes.update(
                            {
                                "bytes_esperados": tamanho_final,
                                "bytes_esperados_pre_download": tamanho_aferido,
                                "bytes_recebidos": tamanho_final,
                                "tamanho_http_verificado": True,
                                "sha256_arquivo_recebido": sha256_recebido,
                                "etag_aferido": etag_aferido,
                                "etag_resposta": etag_resposta,
                            }
                        )
                        log_message(
                            f"Download concluído e conferido: {os.path.basename(caminho_destino)} "
                            f"({formatar_tamanho_bytes(tamanho_final)})",
                            "success",
                        )
                        return True
                    os.remove(caminho_parcial)
                    log_message("O servidor recusou a posição salva; o download será reiniciado.", "warning")
                    continue
                response.raise_for_status()
                if not validar_url_https(response.url):
                    raise requests.exceptions.InvalidURL("O download foi redirecionado para uma URL não HTTPS.")
                content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
                if content_encoding not in ("", "identity"):
                    raise requests.exceptions.InvalidHeader(
                        f"Codificação HTTP inesperada: {content_encoding}."
                    )

                try:
                    content_length_texto = response.headers.get("Content-Length", "").strip()
                    content_length = int(content_length_texto) if content_length_texto else 0
                    modo, inicio, total = parametros_retomada_http(
                        deslocamento,
                        response.status_code,
                        response.headers.get("Content-Range", ""),
                        content_length,
                    )
                except ValueError as erro:
                    raise requests.exceptions.InvalidHeader(str(erro)) from erro
                if total:
                    log_message(
                        f"Tamanho anunciado para {os.path.basename(caminho_destino)}: "
                        f"{formatar_tamanho_bytes(total)}"
                    )
                else:
                    log_message(
                        f"O servidor não anunciou o tamanho de {os.path.basename(caminho_destino)}; "
                        "a conclusão HTTP não poderá ser conferida por tamanho.",
                        "warning",
                    )
                if tamanho_aferido is not None and total and total != tamanho_aferido:
                    raise requests.exceptions.InvalidHeader(
                        f"O tamanho remoto mudou: inventário={tamanho_aferido}; resposta={total}."
                    )
                etag_resposta = response.headers.get("ETag")
                if etag_aferido and etag_resposta and etag_aferido != etag_resposta:
                    raise requests.exceptions.InvalidHeader(
                        "O identificador remoto (ETag) mudou depois do inventário inicial."
                    )
                tamanho_validacao = tamanho_aferido if tamanho_aferido is not None else total
                update_progress(0, f"Baixando {os.path.basename(caminho_destino)}...")

                digest = hashlib.sha256()
                if modo == "ab" and inicio:
                    with open(caminho_parcial, "rb") as parcial_existente:
                        for bloco in iter(lambda: parcial_existente.read(4 * 1024 * 1024), b""):
                            digest.update(bloco)
                with open(caminho_parcial, modo) as arquivo:
                    baixado = inicio
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            arquivo.write(chunk)
                            digest.update(chunk)
                            baixado += len(chunk)
                            if total:
                                update_progress(min(100, int((baixado / total) * 100)))

            tamanho_final = os.path.getsize(caminho_parcial)
            try:
                tamanho_verificado = validar_tamanho_download(tamanho_final, tamanho_validacao)
            except ValueError as erro:
                detalhes["incidentes"].append(
                    {
                        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "tentativa": tentativas,
                        "categoria": "TRANSPORTE_HTTP",
                        "codigo": "HTTP_SIZE_MISMATCH",
                        "mensagem": str(erro),
                        "recuperado": tentativas < max_tentativas,
                    }
                )
                log_message(str(erro), "error" if tentativas == max_tentativas else "warning")
                raise requests.exceptions.ChunkedEncodingError(str(erro)) from erro
            sha256_recebido = digest.hexdigest().upper()
            os.replace(caminho_parcial, caminho_destino)
            detalhes.update(
                {
                    "bytes_esperados": tamanho_validacao or None,
                    "bytes_esperados_pre_download": tamanho_aferido,
                    "bytes_recebidos": tamanho_final,
                    "tamanho_http_verificado": tamanho_verificado,
                    "sha256_arquivo_recebido": sha256_recebido,
                    "etag_aferido": etag_aferido,
                    "etag_resposta": etag_resposta,
                }
            )
            if tamanho_verificado:
                log_message(
                    f"Download concluído e conferido: {os.path.basename(caminho_destino)} "
                    f"({formatar_tamanho_bytes(tamanho_final)})",
                    "success",
                )
            else:
                log_message(
                    f"Download recebido sem conferência HTTP de tamanho: "
                    f"{os.path.basename(caminho_destino)} ({formatar_tamanho_bytes(tamanho_final)}). "
                    "A integridade ainda será validada pelo GPG e pelo ZIP.",
                    "warning",
                )
            return True

        except requests.exceptions.RequestException as e:
            detalhes["incidentes"].append(
                {
                    "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "tentativa": tentativas,
                    "categoria": "REDE_OU_ORIGEM",
                    "codigo": type(e).__name__,
                    "mensagem": "A transferência HTTPS não terminou normalmente.",
                    "recuperado": tentativas < max_tentativas,
                }
            )
            error_msg = f"Falha no download de {url_log} (tentativa {tentativas}): {type(e).__name__}"
            nivel = "error" if tentativas == max_tentativas else "warning"
            log_message(error_msg, nivel)
            
            if tentativas < max_tentativas:
                time.sleep(min(5 * tentativas, 15))
            
            if tentativas == max_tentativas:
                log_message(f"❌ Falha definitiva no download de {url_log}", "error")
                return False

        except OSError as e:
            detalhes["incidentes"].append(
                {
                    "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "tentativa": tentativas,
                    "categoria": "AMBIENTE_SERVIDOR",
                    "codigo": type(e).__name__,
                    "mensagem": "Falha ao gravar o arquivo no disco ou compartilhamento de rede.",
                    "recuperado": False,
                }
            )
            log_message(f"Falha ao salvar o download: {str(e)}", "error")
            return False

    return False

def descriptografar_arquivo(caminho_arquivo, senha, destino, max_tentativas=2):
    gpg_executavel = localizar_gpg()
    if not gpg_executavel:
        log_message(
            "GPG não encontrado. Instale o Gpg4win ou coloque gpg.exe em gpg\\bin ao lado do Nêmesis.",
            "error",
        )
        return False

    tentativas = 0
    destino_parcial = f"{destino}.part"
    limite_segundos = calcular_limite_descriptografia(os.path.getsize(caminho_arquivo))
    while tentativas < max_tentativas:
        tentativas += 1
        try:
            log_message(f"Tentativa {tentativas}/{max_tentativas} - Descriptografando {os.path.basename(caminho_arquivo)}")
            update_progress(0, f"Descriptografando {os.path.basename(caminho_arquivo)}")
            
            command = [
                gpg_executavel,
                "--batch",
                "--yes",
                "--pinentry-mode",
                "loopback",
                "--passphrase-fd",
                "0",
                "--output",
                destino_parcial,
                "--decrypt",
                caminho_arquivo,
            ]
            result = subprocess.run(
                command,
                input=f"{senha}\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=limite_segundos,
            )
            
            if result.returncode == 0:
                os.replace(destino_parcial, destino)
                log_message(f"Descriptografia concluída: {os.path.basename(destino)}", "success")
                return True
            else:
                try:
                    os.remove(destino_parcial)
                except OSError:
                    pass
                error_msg = f"Erro na descriptografia (tentativa {tentativas}): {result.stderr.strip()}"
                log_message(error_msg, "error")
                
                if "bad passphrase" in result.stderr.lower():
                    registrar_incidente(
                        "CREDENCIAL_FORNECIDA",
                        "descriptografia",
                        "GPG_BAD_PASSPHRASE",
                        "O GPG recusou a senha informada.",
                        item=caminho_arquivo,
                        tentativa=tentativas,
                    )
                    log_message("❌ Senha incorreta. Interrompendo tentativas.", "error")
                    return False
                registrar_incidente(
                    "CONTEUDO_OU_ORIGEM",
                    "descriptografia",
                    "GPG_DECRYPTION_FAILED",
                    "O GPG recusou ou não conseguiu validar o conteúdo recebido.",
                    item=caminho_arquivo,
                    tentativa=tentativas,
                )
                
                time.sleep(2)

        except subprocess.TimeoutExpired:
            try:
                os.remove(destino_parcial)
            except OSError:
                pass
            log_message(
                f"Falha ao descriptografar (tentativa {tentativas}): o GPG excedeu "
                f"{limite_segundos // 60} minutos.",
                "error",
            )
            registrar_incidente(
                "AMBIENTE_SERVIDOR",
                "descriptografia",
                "GPG_TIMEOUT",
                f"A descriptografia excedeu o limite de {limite_segundos // 60} minutos.",
                item=caminho_arquivo,
                tentativa=tentativas,
            )
            time.sleep(2)
        except Exception as e:
            error_msg = f"Falha ao descriptografar (tentativa {tentativas}): {str(e)}"
            log_message(error_msg, "error")
            registrar_incidente(
                "AMBIENTE_SERVIDOR",
                "descriptografia",
                type(e).__name__,
                "O processo local do GPG não conseguiu concluir a operação.",
                item=caminho_arquivo,
                tentativa=tentativas,
            )
            time.sleep(2)
    
    log_message(f"❌ Falha definitiva na descriptografia de {os.path.basename(caminho_arquivo)}", "error")
    return False

def extrair_todos_zips(pasta_zips, pasta_destino, max_tentativas=2):
    try:
        arquivos_zip = [f for f in os.listdir(pasta_zips) if f.lower().endswith(".zip")]
        total_arquivos = len(arquivos_zip)
        
        if total_arquivos == 0:
            log_message("Nenhum arquivo ZIP encontrado para extração", "error")
            return set()

        log_message(f"Iniciando extração de {total_arquivos} arquivos ZIP...")
        update_progress(0, "Preparando extração...")

        tamanho_descompactado = 0
        for arquivo_zip in arquivos_zip:
            caminho_zip = os.path.join(pasta_zips, arquivo_zip)
            try:
                with zipfile.ZipFile(caminho_zip, "r") as zipf:
                    tamanho_descompactado += sum(membro.file_size for membro in zipf.infolist())
            except zipfile.BadZipFile:
                # O erro específico será informado durante a extração.
                continue

        os.makedirs(pasta_destino, exist_ok=True)
        espaco_livre = shutil.disk_usage(pasta_destino).free
        margem_seguranca = max(512 * 1024 * 1024, tamanho_descompactado // 20)
        necessario = tamanho_descompactado + margem_seguranca
        if necessario > espaco_livre:
            necessario_gb = necessario / (1024 ** 3)
            livre_gb = espaco_livre / (1024 ** 3)
            log_message(
                f"❌ Espaço insuficiente para extração: necessário aproximadamente "
                f"{necessario_gb:.2f} GB; disponível {livre_gb:.2f} GB.",
                "error",
            )
            return set()
        
        extraidos_com_sucesso = set()
        problemas_encontrados = 0

        for idx, arquivo_zip in enumerate(arquivos_zip, 1):
            caminho_zip = os.path.join(pasta_zips, arquivo_zip)
            tentativas = 0
            sucesso = False
            
            while tentativas < max_tentativas and not sucesso:
                tentativas += 1
                try:
                    relatorio_extracao = {}
                    quantidade = extrair_zip_seguro(
                        caminho_zip,
                        pasta_destino,
                        relatorio_extracao,
                    )
                    renomeados = int(relatorio_extracao.get("renomeados", 0))
                    if renomeados:
                        log_message(
                        f"ℹ️ {arquivo_zip}: {renomeados} entrada(s) tinham indicador de "
                        "raiz externa; somente esse separador inicial foi removido. O mapa foi salvo "
                            "fora da pasta extraída, junto aos ZIPs temporários.",
                            "info",
                        )
                    
                    extraidos_com_sucesso.add(arquivo_zip)
                    log_message(
                        f"✅ {arquivo_zip} extraído com sucesso ({quantidade} arquivos, tentativa {tentativas})"
                    )
                    sucesso = True
                    
                except zipfile.BadZipFile as e:
                    if tentativas == max_tentativas:
                        problemas_encontrados += 1
                        log_message(f"❌ {arquivo_zip} está corrompido: {str(e)}", "error")
                except ValueError as e:
                    problemas_encontrados += 1
                    log_message(f"❌ {arquivo_zip} foi bloqueado por segurança: {str(e)}", "error")
                    break
                except Exception as e:
                    if tentativas == max_tentativas:
                        problemas_encontrados += 1
                        log_message(f"⚠️ Erro em {arquivo_zip} (tentativa {tentativas}): {str(e)}", "warning")
                    time.sleep(1)
            
            percentual = int((idx / total_arquivos) * 100)
            update_progress(percentual, f"Extraindo... ({idx}/{total_arquivos})")

        if problemas_encontrados == 0:
            log_message("✅ Todos os arquivos extraídos com sucesso!", "success")
            return extraidos_com_sucesso
        elif extraidos_com_sucesso:
            log_message(f"⚠️ Extração parcial: {len(extraidos_com_sucesso)} sucessos, {problemas_encontrados} falhas", "warning")
            return extraidos_com_sucesso
        else:
            log_message("❌ Falha em todos os arquivos ZIP", "error")
            return set()

    except Exception as e:
        log_message(f"❌ ERRO GRAVE: {str(e)}", "error")
        return set()
    
def coletar_links_https(pasta_base):
    pasta_links = os.path.join(pasta_base, "Account_Data_Links")
    if not os.path.exists(pasta_links):
        log_message("❌ Pasta 'Account_Data_Links' não encontrada", "error")
        return {}

    # Variável para acumular o tamanho total
    total_size_bytes = 0
    html_files = [f for f in os.listdir(pasta_links) if f.endswith(".html")]
    total_arquivos = len(html_files)
    
    if total_arquivos == 0:
        log_message("ℹ️ Nenhum arquivo de conta (.html) encontrado", "info")
        return {}

    # Calcular tamanho total
    for arquivo in html_files:
        caminho_arquivo = os.path.join(pasta_links, arquivo)
        total_size_bytes += os.path.getsize(caminho_arquivo)
    
    # Converter para GB
    total_size_gb = total_size_bytes / (1024 ** 3)
    size_str = f"{total_size_gb:.2f} GB"
    
    # Exibir o total no log
    log_message(f"📊 Tamanho total dos arquivos HTML: {size_str}")
    
    contas_encontradas = {}
    log_message(f"🔍 Encontradas {total_arquivos} contas para processar")
    
    for arquivo in html_files:
        if arquivo not in arquivos_html_processados:
            nome_conta = os.path.splitext(arquivo)[0]
            caminho_arquivo = os.path.join(pasta_links, arquivo)
            
            contas_encontradas[nome_conta] = {
                "arquivo": arquivo,
                "produtos": defaultdict(list),
                "metadados": {},
                "status": "Pendente"
            }
            
            try:
                with open(caminho_arquivo, "r", encoding="utf-8", errors="ignore") as f:
                    conteudo = f.read()
                
                padrao = extrair_links_html_detalhados(conteudo)
                
                if not padrao:
                    log_message(f"⚠️ Conta '{nome_conta}' não contém links de produtos", "warning")
                    contas_encontradas[nome_conta]["status"] = "Sem produtos"
                    continue
                
                for item in padrao:
                    id_tag = item["produto"]
                    link = item["url"]
                    contas_encontradas[nome_conta]["produtos"][id_tag].append(link)
                    contas_encontradas[nome_conta]["metadados"][link] = {
                        "produto": id_tag,
                        "nome_produto": item.get("nome_produto") or nome_pasta_produto(id_tag),
                        "nome_arquivo_original": item.get("nome_arquivo_original")
                        or nome_arquivo_original_url(link),
                    }
                    status_links.setdefault(link, "pendente")
                
                total_produtos = sum(len(links) for links in contas_encontradas[nome_conta]["produtos"].values())
                log_message(f"📄 Conta '{nome_conta}': {len(padrao)} links encontrados em {total_produtos} produtos")
                contas_encontradas[nome_conta]["status"] = "Processado"
                
                arquivos_html_processados.add(arquivo)
                
            except Exception as e:
                log_message(f"❌ Erro ao processar conta '{nome_conta}': {str(e)}", "error")
                contas_encontradas[nome_conta]["status"] = f"Erro: {str(e)}"
                continue
    
    for conta, dados in contas_encontradas.items():
        if conta not in contas_processadas:
            contas_processadas[conta] = dados
    
    total_contas = len(contas_encontradas)
    contas_ok = sum(1 for c in contas_encontradas.values() if c["status"] == "Processado")
    
    if contas_ok > 0:
        log_message(f"✅ {contas_ok}/{total_contas} contas processadas com sucesso", "success")
    else:
        log_message("⚠️ Nenhuma conta foi processada com sucesso", "warning")
    
    todos_produtos = defaultdict(list)
    for conta in contas_encontradas.values():
        for id_tag, links in conta["produtos"].items():
            todos_produtos[id_tag].extend(links)
    
    return todos_produtos

def baixar_todos_arquivos(links, pasta_temp, forcar_download=False):
    os.makedirs(pasta_temp, exist_ok=True)
    arquivos_baixados = []
    total_links = sum(len(links) for links in links.values())
    
    if total_links == 0:
        log_message("ℹ️ Nenhum produto para baixar", "info")
        return arquivos_baixados
    
    log_message(f"⏬ Iniciando download de {total_links} arquivos de produtos...")
    links_processados = 0
    erros = 0
    
    for id_tag, lista_links in links.items():
        status_downloads[id_tag] = f"⌛ Baixando {len(lista_links)} arquivos..."
        atualizar_lista()
        
        for i, link in enumerate(lista_links, 1):
            if link in historico_downloads and not forcar_download:
                status_links.setdefault(link, "baixado")
                log_message(f"🔁 Produto {id_tag} (arquivo {i}): Já baixado anteriormente", "info")
                links_processados += 1
                continue

            update_progress(int((links_processados / total_links) * 100), 
                          f"Baixando produto {id_tag} ({i}/{len(lista_links)})...")
            
            nome_arquivo = nome_download_estavel(id_tag, link)
            caminho_gpg = os.path.join(pasta_temp, nome_arquivo)
            
            log_message(f"⬇️ Baixando produto {id_tag} (arquivo {i}/{len(lista_links)})...")
            
            if baixar_arquivo(link, caminho_gpg):
                arquivos_baixados.append((id_tag, caminho_gpg, link))
                status_downloads[id_tag] = f"✅ Baixado {i}/{len(lista_links)}"
                historico_downloads.add(link)
                status_links[link] = "baixado"
                links_processados += 1
                log_message(f"✔️ Produto {id_tag} (arquivo {i}): Download concluído", "success")
            else:
                status_downloads[id_tag] = f"❌ Erro {i}/{len(lista_links)}"
                status_links[link] = "erro_download"
                log_message(f"❌ Falha no download do produto {id_tag} (arquivo {i})", "error")
                erros += 1
                registrar_erro_conta(id_tag, "erros", link)
            
            atualizar_lista()
    
    update_progress(100, "Downloads concluídos")
    log_message(f"📊 Total de downloads: {links_processados}/{total_links} arquivos baixados ({erros} erros)", 
               "success" if erros == 0 else "warning")
    
    for conta in contas_processadas.values():
        todos_baixados = all(
            link in historico_downloads
            for links in conta["produtos"].values()
            for link in links
        )
        conta["status"] = "✅ Completo" if todos_baixados else f"⚠️ Parcial ({len(conta.get('produtos', {})) - len(conta.get('erros', []))}/{len(conta.get('produtos', {}))})"
    
    return arquivos_baixados

def processar_todos_arquivos(arquivos_baixados, senha, pasta_zips):
    os.makedirs(pasta_zips, exist_ok=True)
    total_arquivos = len(arquivos_baixados)
    erros = 0
    zips_prontos = set()
    totais_por_produto = defaultdict(int)
    sucessos_por_produto = defaultdict(int)
    
    if total_arquivos == 0:
        log_message("Nenhum arquivo para processar", "warning")
        return zips_prontos
    
    log_message(f"Iniciando processamento de {total_arquivos} arquivos...")
    
    for i, (id_tag, caminho_gpg, link) in enumerate(arquivos_baixados, 1):
        totais_por_produto[id_tag] += 1
        status_downloads[id_tag] = f"Descriptografando {i}/{total_arquivos}..."
        atualizar_lista()
        update_progress(int((i / total_arquivos) * 100), f"Processando {id_tag}...")

        nome_zip = f"{os.path.splitext(os.path.basename(caminho_gpg))[0]}.zip"
        caminho_zip = os.path.join(pasta_zips, nome_zip)
        
        if descriptografar_arquivo(caminho_gpg, senha, caminho_zip):
            zips_prontos.add(os.path.basename(caminho_zip))
            sucessos_por_produto[id_tag] += 1
            status_links[link] = "pronto_extracao"
            status_downloads[id_tag] = f"Pronto para extração {i}/{total_arquivos}"
        else:
            status_links[link] = "erro_descriptografia"
            status_downloads[id_tag] = f"❌ Erro descriptografia {i}/{total_arquivos}"
            erros += 1
            registrar_erro_conta(id_tag, "erros_processamento", caminho_gpg)
        
        atualizar_lista()
    
    for id_tag, total_produto in totais_por_produto.items():
        sucessos_produto = sucessos_por_produto[id_tag]
        if sucessos_produto == total_produto:
            status_downloads[id_tag] = "Pronto para extração"
        else:
            status_downloads[id_tag] = (
                f"❌ Erro na descriptografia ({sucessos_produto}/{total_produto} concluídos)"
            )

    update_progress(100, "Processamento concluído")
    if erros > 0:
        log_message(f"⚠️ Descriptografia parcial: {total_arquivos - erros} sucessos, {erros} falhas", "warning")
    else:
        log_message("✅ Descriptografia concluída com sucesso!", "success")
    
    return zips_prontos


def atualizar_status_apos_extracao(arquivos_baixados, zips_extraidos):
    """Atualiza cada produto sem transformar falhas parciais em falsos sucessos."""
    esperados_por_produto = defaultdict(set)
    links_por_zip = {}
    zips_extraidos = set(zips_extraidos)

    for id_tag, caminho_gpg, link in arquivos_baixados:
        nome_zip = f"{os.path.splitext(os.path.basename(caminho_gpg))[0]}.zip"
        esperados_por_produto[id_tag].add(nome_zip)
        links_por_zip[nome_zip] = link

    todos_concluidos = True
    for id_tag, esperados in esperados_por_produto.items():
        quantidade_extraida = len(esperados & zips_extraidos)
        if quantidade_extraida == len(esperados):
            status_downloads[id_tag] = "✅ Concluído"
        else:
            todos_concluidos = False
            if "descriptografia" not in status_downloads.get(id_tag, "").lower():
                status_downloads[id_tag] = (
                    f"❌ Erro na extração ({quantidade_extraida}/{len(esperados)} concluídos)"
                )

        for nome_zip in esperados:
            link = links_por_zip[nome_zip]
            if nome_zip in zips_extraidos:
                status_links[link] = "concluido"
            elif status_links.get(link) != "erro_descriptografia":
                status_links[link] = "erro_extracao"

    return todos_concluidos

def processar_contas_sequencialmente(contas, pasta_temp, pasta_zips, pasta_final, senha):
    for conta, dados_conta in contas.items():
        arquivos_baixados = baixar_conta_sequencial(conta, dados_conta, pasta_temp)
        
        if not arquivos_baixados:
            continue
            
        log_message(f"\n🔐 PROCESSANDO ARQUIVOS DA CONTA: {conta}")
        zips_prontos = processar_todos_arquivos(arquivos_baixados, senha, pasta_zips)
        if zips_prontos:
            log_message(f"\n📂 EXTRAINDO ARQUIVOS DA CONTA: {conta}")
            zips_extraidos = extrair_todos_zips(pasta_zips, pasta_final)
            if zips_extraidos:
                if atualizar_status_apos_extracao(arquivos_baixados, zips_extraidos):
                    dados_conta["status"] = "✅ Processamento completo"
                    log_message(f"✔️ Conta {conta} processada com sucesso!", "success")
                else:
                    dados_conta["status"] = "⚠️ Processamento parcial"
                    log_message(f"⚠️ A conta {conta} ficou com arquivos pendentes", "warning")
            else:
                dados_conta["status"] = "⚠️ Extração incompleta"
                atualizar_status_apos_extracao(arquivos_baixados, set())
                log_message(f"⚠️ Erro na extração dos arquivos da conta {conta}", "error")
        else:
            dados_conta["status"] = "⚠️ Processamento incompleto"
            log_message(f"⚠️ Erro no processamento dos arquivos da conta {conta}", "error")
        
        atualizar_lista()
        
        try:
            log_message(f"\n🧹 LIMPANDO ARQUIVOS TEMPORÁRIOS DA CONTA: {conta}")
            for _, caminho_gpg, _ in arquivos_baixados:
                if os.path.exists(caminho_gpg):
                    os.remove(caminho_gpg)
                    log_message(f"   ✔️ Removido: {os.path.basename(caminho_gpg)}", "info")
            
            for id_tag in dados_conta["produtos"]:
                padrao_zip = f"{nome_arquivo_seguro(id_tag)}_*.zip"
                for zip_file in glob.glob(os.path.join(pasta_zips, padrao_zip)):
                    os.remove(zip_file)
                    log_message(f"   ✔️ Removido: {os.path.basename(zip_file)}", "info")
                    
        except Exception as e:
            log_message(f"   ❌ Erro ao limpar arquivos: {str(e)}", "error")

def baixar_conta_sequencial(conta, dados_conta, pasta_temp):
    arquivos_baixados = []
    produtos = dados_conta["produtos"]
    total_links = sum(len(links) for links in produtos.values())
    links_processados = 0
    
    log_message(f"\n📥 INICIANDO DOWNLOADS PARA CONTA: {conta}")
    dados_conta["status"] = "⬇️ Baixando..."
    atualizar_lista()
    
    for id_tag, lista_links in produtos.items():
        status_downloads[id_tag] = f"⌛ Aguardando ({len(lista_links)} arquivos)"
        atualizar_lista()
        
        for i, link in enumerate(lista_links, 1):
            if link in historico_downloads:
                status_links.setdefault(link, "baixado")
                log_message(f"   🔄 Produto {id_tag} (arquivo {i}): Já baixado", "info")
                links_processados += 1
                continue

            update_progress(int((links_processados / total_links) * 100),
                          f"Baixando {conta} - {id_tag} ({i}/{len(lista_links)})")
            
            nome_arquivo = nome_download_estavel(id_tag, link)
            caminho_gpg = os.path.join(pasta_temp, nome_arquivo)
            
            log_message(f"   ⬇️ Baixando produto {id_tag} (arquivo {i}/{len(lista_links)})...")
            
            if baixar_arquivo(link, caminho_gpg):
                arquivos_baixados.append((id_tag, caminho_gpg, link))
                status_downloads[id_tag] = f"✅ {i}/{len(lista_links)} baixados"
                historico_downloads.add(link)
                status_links[link] = "baixado"
                links_processados += 1
                log_message(f"   ✔️ Concluído: {id_tag} (arquivo {i})", "success")
            else:
                status_downloads[id_tag] = f"❌ Erro {i}/{len(lista_links)}"
                status_links[link] = "erro_download"
                log_message(f"   ❌ Falha no download: {id_tag} (arquivo {i})", "error")
            
            atualizar_lista()
    
    if links_processados == total_links:
        dados_conta["status"] = "✅ Download completo"
        log_message(f"✔️ TODOS os arquivos da conta {conta} foram baixados", "success")
    else:
        dados_conta["status"] = f"⚠️ Download parcial ({links_processados}/{total_links})"
        log_message(f"⚠️ Download PARCIAL da conta {conta}: {links_processados}/{total_links} arquivos", "warning")
    
    atualizar_lista()
    return arquivos_baixados

def encontrar_pasta_conta(pasta_base):
    for root, dirs, files in os.walk(pasta_base):
        for dir_name in dirs:
            if '@' in dir_name:
                return os.path.join(root, dir_name)
    return pasta_base


def construir_origens_links():
    """Mantém a conta e o produto de origem de cada URL do manifesto."""
    origens = defaultdict(list)
    for conta, dados_conta in contas_processadas.items():
        for produto, links in dados_conta.get("produtos", {}).items():
            for link in links:
                origem = {"conta": conta, "produto": produto}
                if origem not in origens[link]:
                    origens[link].append(origem)
    return dict(origens)


def construir_metadados_links():
    """Reúne o nome visível e o nome original de cada pacote do manifesto Apple."""
    metadados = {}
    for dados_conta in contas_processadas.values():
        for link, dados in dados_conta.get("metadados", {}).items():
            if isinstance(dados, dict):
                metadados.setdefault(link, dict(dados))
    return metadados


def salvar_estado_atual(status=None, fase=None):
    """Persiste o ponto exato da execução sem armazenar a senha GPG."""
    if not caminho_estado_atual or not estado_execucao:
        return
    with lock_estado:
        if status:
            estado_execucao["status"] = status
        if fase:
            estado_execucao["fase"] = fase
        estado_execucao["updated_at"] = datetime.now().isoformat(timespec="seconds")
        estado_execucao["accounts"] = copy.deepcopy(contas_processadas)
        estado_execucao["products"] = {chave: list(valores) for chave, valores in links_armazenados.items()}
        estado_execucao["link_status"] = dict(status_links)
        salvar_estado_atomico(caminho_estado_atual, estado_execucao)
        try:
            gerar_relatorios_auditoria(estado_execucao, caminho_log_atual)
        except Exception as erro:
            registrar_incidente(
                "SISTEMA_NEMESIS",
                "auditoria",
                type(erro).__name__,
                "Não foi possível atualizar o relatório de auditoria nesta tentativa.",
            )
            salvar_estado_atomico(caminho_estado_atual, estado_execucao)
            log_message(
                f"Falha ao atualizar o relatório de auditoria: {type(erro).__name__}",
                "error",
            )


def carregar_execucao(caminho_estado):
    global estado_execucao, caminho_estado_atual, caminho_log_atual
    global links_armazenados, contas_processadas

    estado = carregar_estado(caminho_estado)
    estado_execucao = estado
    caminho_estado_atual = str(caminho_estado)
    raiz = Path(caminho_estado).parent
    caminho_log_atual = str(raiz / ARQUIVO_LOG)

    historico_downloads.clear()
    status_downloads.clear()
    status_links.clear()
    arquivos_html_processados.clear()
    contas_processadas.clear()
    contas_processadas.update(estado.get("accounts", {}))
    links_armazenados = defaultdict(list, estado.get("products", {}))
    estado_execucao.setdefault("link_origins", construir_origens_links())
    estado_execucao.setdefault("product_metadata", construir_metadados_links())
    status_links.update(estado.get("link_status", {}))
    for link, etapa in status_links.items():
        if etapa not in ("pendente", "erro_download"):
            historico_downloads.add(link)
    for id_tag in links_armazenados:
        status_downloads[id_tag] = "⏳ Pendente"


def extrair_um_zip(caminho_zip, pasta_destino, max_tentativas=2, relatorio=None, permitir_recuperacao=False):
    """Valida espaço e extrai somente um ZIP, preservando-o em caso de falha."""
    if relatorio is not None:
        relatorio.clear()
    try:
        with zipfile.ZipFile(caminho_zip, "r") as arquivo_zip:
            tamanho = sum(membro.file_size for membro in arquivo_zip.infolist())
        os.makedirs(pasta_destino, exist_ok=True)
        livre = shutil.disk_usage(pasta_destino).free
        margem = max(512 * 1024 * 1024, tamanho // 20)
        if tamanho + margem > livre:
            log_message(
                f"Espaço insuficiente: necessário aproximadamente {(tamanho + margem) / (1024 ** 3):.2f} GB; "
                f"disponível {livre / (1024 ** 3):.2f} GB.",
                "error",
            )
            return False
    except zipfile.BadZipFile as erro:
        registrar_incidente(
            "CONTEUDO_OU_ORIGEM",
            "validacao_zip",
            "ZIP_CORRUPT_OR_INCOMPLETE",
            "O ZIP descriptografado falhou na validação de integridade.",
            item=caminho_zip,
        )
        log_message(f"ZIP inválido ou inacessível: {erro}", "error")
        return False
    except OSError as erro:
        registrar_incidente(
            "AMBIENTE_SERVIDOR",
            "validacao_zip",
            type(erro).__name__,
            "O servidor não conseguiu acessar ou preparar o ZIP para extração.",
            item=caminho_zip,
        )
        log_message(f"ZIP inválido ou inacessível: {erro}", "error")
        return False

    for tentativa in range(1, max_tentativas + 1):
        try:
            relatorio_extracao = {}
            try:
                quantidade = extrair_zip_seguro(caminho_zip, pasta_destino, relatorio_extracao)
            except (FileExistsError, NomeZipIncompativel) as conflito:
                if not permitir_recuperacao:
                    raise
                log_message(f"Conflito detectado; recuperando o ZIP em pasta isolada: {conflito}", "warning")
                quantidade = recuperar_zip_com_mapa(caminho_zip, pasta_destino, relatorio_extracao)
                log_message(
                    f"Recuperação concluída com nomes rastreáveis; ZIP original preservado: {caminho_zip}. "
                    f"Mapa: {relatorio_extracao.get('mapa')}", "warning"
                )
            renomeados = int(relatorio_extracao.get("renomeados", 0))
            if renomeados and not relatorio_extracao.get("recuperacao_isolada"):
                log_message(
                    f"{renomeados} entrada(s) de {os.path.basename(caminho_zip)} tinham "
                    "indicador de raiz externa; somente esse separador inicial foi removido. O mapa foi "
                    "salvo fora da pasta extraída, junto aos ZIPs temporários.",
                    "info",
                )
            reutilizados = int(relatorio_extracao.get("arquivos_reutilizados_identicos", 0))
            if reutilizados:
                log_message(
                    f"{reutilizados} arquivo(s) já existiam com conteúdo idêntico e foram "
                    "conferidos sem substituição.",
                    "info",
                )
            if relatorio is not None:
                relatorio.update(relatorio_extracao)
            for aviso in relatorio_extracao.get("avisos_origem", []):
                quantidade_itens = int(aviso.get("quantidade_itens") or 0)
                arquivo_relatorio = aviso.get("arquivo_relatorio") or os.path.basename(caminho_zip)
                log_message(
                    f"A Apple declarou falha na criação de {quantidade_itens} item(ns) "
                    f"no relatório {arquivo_relatorio}. Isso ocorreu antes do download pelo Nêmesis.",
                    "warning",
                )
                registrar_incidente(
                    "ORIGEM_APPLE",
                    "criacao_pacote_origem",
                    str(aviso.get("codigo") or "APPLE_SOURCE_WARNING"),
                    f"A origem declarou {quantidade_itens} item(ns) ausentes do pacote.",
                    item=arquivo_relatorio,
                )
            log_message(
                f"Extração concluída: {os.path.basename(caminho_zip)} "
                f"({quantidade} arquivos; "
                f"{formatar_tamanho_bytes(int(relatorio_extracao.get('bytes_extraidos', 0)))})",
                "success",
            )
            return True
        except FileExistsError as erro:
            registrar_incidente(
                "CONFLITO_DE_CONTEUDO",
                "extracao_zip",
                "EXTRACTION_PATH_CONFLICT",
                str(erro),
                item=caminho_zip,
                tentativa=tentativa,
            )
            log_message(f"Extração interrompida para impedir substituição: {erro}", "error")
            return False
        except ValueError as erro:
            registrar_incidente(
                "CONTEUDO_OU_ORIGEM",
                "extracao_zip",
                "ZIP_PATH_BLOCKED",
                str(erro),
                item=caminho_zip,
                tentativa=tentativa,
            )
            log_message(f"ZIP bloqueado por segurança: {erro}", "error")
            return False
        except (OSError, zipfile.BadZipFile) as erro:
            categoria = "CONTEUDO_OU_ORIGEM" if isinstance(erro, zipfile.BadZipFile) else "AMBIENTE_SERVIDOR"
            registrar_incidente(
                categoria,
                "extracao_zip",
                type(erro).__name__,
                "A extração não foi concluída nesta tentativa.",
                item=caminho_zip,
                tentativa=tentativa,
            )
            nivel = "error" if tentativa == max_tentativas else "warning"
            log_message(f"Falha na extração (tentativa {tentativa}/{max_tentativas}): {erro}", nivel)
            if tentativa < max_tentativas:
                time.sleep(1)
    return False


def preparar_principal_e_links(senha):
    """Obtém o manifesto principal e persiste as contas e URLs encontradas."""
    global links_armazenados
    caminhos = estado_execucao["paths"]
    pasta_temp = caminhos["temp"]
    pasta_contas = caminhos["contas"]
    os.makedirs(pasta_temp, exist_ok=True)
    os.makedirs(pasta_contas, exist_ok=True)

    if links_armazenados:
        destino = estado_execucao.get("product_destination") or encontrar_pasta_conta(pasta_contas)
        estado_execucao["product_destination"] = destino
        return destino

    caminho_gpg = os.path.join(pasta_temp, "principal.gpg")
    caminho_zip = os.path.join(pasta_temp, "principal.zip")
    if not os.path.exists(caminho_zip):
        if not os.path.exists(caminho_gpg):
            log_message("BAIXANDO ARQUIVO PRINCIPAL")
            relatorio_download = {}
            download_principal_ok = baixar_arquivo(
                estado_execucao["source_url"],
                caminho_gpg,
                relatorio=relatorio_download,
            )
            estado_execucao["principal_download_check"] = relatorio_download
            if not download_principal_ok:
                salvar_estado_atual("parcial", "principal")
                return None
            salvar_estado_atual("em_andamento", "principal")
        log_message("DESCRIPTOGRAFANDO ARQUIVO PRINCIPAL")
        if not descriptografar_arquivo(caminho_gpg, senha, caminho_zip):
            salvar_estado_atual("parcial", "principal")
            return None
        try:
            os.remove(caminho_gpg)
        except OSError:
            pass

    log_message("EXTRAINDO ARQUIVO PRINCIPAL")
    relatorio_principal = {}
    if not extrair_um_zip(caminho_zip, pasta_contas, relatorio=relatorio_principal):
        salvar_estado_atual("parcial", "principal")
        return None
    estado_execucao["principal_extraction_check"] = relatorio_principal
    try:
        os.remove(caminho_zip)
    except OSError:
        pass

    pasta_conta = encontrar_pasta_conta(pasta_contas)
    links_armazenados = defaultdict(list, coletar_links_https(pasta_conta))
    estado_execucao["link_origins"] = construir_origens_links()
    estado_execucao["product_metadata"] = construir_metadados_links()
    estado_execucao["product_destination"] = pasta_conta
    for id_tag in links_armazenados:
        status_downloads[id_tag] = "⏳ Aguardando"
    salvar_estado_atual("em_andamento", "produtos")
    atualizar_lista()
    return pasta_conta


def registrar_resumo_integridade(total_itens):
    """Persiste e exibe a auditoria de tamanhos da execução."""
    downloads = estado_execucao.setdefault("download_checks", {})
    extracoes = estado_execucao.setdefault("extraction_checks", {})
    relatorios_download = [valor for valor in downloads.values() if isinstance(valor, dict)]
    relatorios_extracao = [valor for valor in extracoes.values() if isinstance(valor, dict)]
    recebidos = sum(int(item.get("bytes_recebidos") or 0) for item in relatorios_download)
    esperados = sum(int(item.get("bytes_esperados") or 0) for item in relatorios_download)
    verificados_http = sum(bool(item.get("tamanho_http_verificado")) for item in relatorios_download)
    sem_tamanho = len(relatorios_download) - verificados_http
    bytes_extraidos = sum(int(item.get("bytes_extraidos") or 0) for item in relatorios_extracao)
    bytes_manifestados_zip = sum(
        int(item.get("bytes_manifestados_zip") or 0) for item in relatorios_extracao
    )
    bytes_reutilizados = sum(
        int(item.get("bytes_reutilizados_identicos") or 0) for item in relatorios_extracao
    )
    arquivos_manifestados_zip = sum(
        int(item.get("arquivos_manifestados_zip") or 0) for item in relatorios_extracao
    )
    arquivos_reutilizados = sum(
        int(item.get("arquivos_reutilizados_identicos") or 0) for item in relatorios_extracao
    )
    extracoes_integrais = all(
        int(item.get("bytes_manifestados_zip") or 0)
        == int(item.get("bytes_extraidos") or 0)
        + int(item.get("bytes_reutilizados_identicos") or 0)
        and int(item.get("arquivos_manifestados_zip") or 0)
        == int(item.get("extraidos") or 0)
        + int(item.get("arquivos_reutilizados_identicos") or 0)
        for item in relatorios_extracao
    )
    avisos_origem = len(
        estado_execucao.get("principal_extraction_check", {}).get("avisos_origem") or []
    ) + sum(
        len(item.get("avisos_origem") or [])
        for item in relatorios_extracao
    )
    auditoria_completa = (
        len(relatorios_download) == total_itens
        and len(relatorios_extracao) == total_itens
    )
    resumo_remoto = estado_execucao.get("remote_inventory_summary", {})
    bytes_remotos_previstos = int(resumo_remoto.get("bytes_esperados") or 0)
    tamanhos_pre_download_confirmados = bool(
        resumo_remoto.get("completo")
        and auditoria_completa
        and recebidos == bytes_remotos_previstos
        and all(
            int(item.get("bytes_recebidos") or -1)
            == int(item.get("bytes_esperados_pre_download") or -2)
            for item in relatorios_download
        )
    )
    http_totalmente_verificado = (
        auditoria_completa
        and verificados_http == total_itens
        and tamanhos_pre_download_confirmados
    )
    inventario_final = auditar_pasta_extraida(estado_execucao["product_destination"])
    inventario_legivel = not inventario_final.get("erros_leitura")
    integridade_extracao_confirmada = (
        auditoria_completa and extracoes_integrais and inventario_legivel
    )

    por_produto = {}
    for item in relatorios_download:
        produto = str(item.get("produto") or "produto_nao_identificado")
        resumo_produto = por_produto.setdefault(
            produto,
            {
                "nome_produto": item.get("nome_produto") or nome_pasta_produto(produto),
                "pasta_produto": item.get("nome_produto") or nome_pasta_produto(produto),
                "downloads": 0,
                "bytes_recebidos_gpg": 0,
                "zips": 0,
                "arquivos_manifestados_zip": 0,
                "bytes_manifestados_zip": 0,
                "arquivos_gravados": 0,
                "bytes_gravados": 0,
                "arquivos_reutilizados_identicos": 0,
                "bytes_reutilizados_identicos": 0,
            },
        )
        resumo_produto["downloads"] += 1
        resumo_produto["bytes_recebidos_gpg"] += int(item.get("bytes_recebidos") or 0)
    for item in relatorios_extracao:
        produto = str(item.get("produto") or "produto_nao_identificado")
        resumo_produto = por_produto.setdefault(
            produto,
            {
                "nome_produto": item.get("nome_produto") or nome_pasta_produto(produto),
                "pasta_produto": item.get("nome_produto") or nome_pasta_produto(produto),
                "downloads": 0,
                "bytes_recebidos_gpg": 0,
                "zips": 0,
                "arquivos_manifestados_zip": 0,
                "bytes_manifestados_zip": 0,
                "arquivos_gravados": 0,
                "bytes_gravados": 0,
                "arquivos_reutilizados_identicos": 0,
                "bytes_reutilizados_identicos": 0,
            },
        )
        resumo_produto["zips"] += 1
        resumo_produto["nome_produto"] = item.get("nome_produto") or resumo_produto["nome_produto"]
        resumo_produto["pasta_produto"] = item.get("nome_produto") or resumo_produto["pasta_produto"]
        resumo_produto["arquivos_manifestados_zip"] += int(item.get("arquivos_manifestados_zip") or 0)
        resumo_produto["bytes_manifestados_zip"] += int(item.get("bytes_manifestados_zip") or 0)
        resumo_produto["arquivos_gravados"] += int(item.get("extraidos") or 0)
        resumo_produto["bytes_gravados"] += int(item.get("bytes_extraidos") or 0)
        resumo_produto["arquivos_reutilizados_identicos"] += int(
            item.get("arquivos_reutilizados_identicos") or 0
        )
        resumo_produto["bytes_reutilizados_identicos"] += int(
            item.get("bytes_reutilizados_identicos") or 0
        )
    resumo = {
        "arquivos_manifestados": total_itens,
        "downloads_registrados": len(relatorios_download),
        "downloads_http_verificados": verificados_http,
        "downloads_sem_tamanho_http": sem_tamanho,
        "extracoes_registradas": len(relatorios_extracao),
        "bytes_http_esperados_conhecidos": esperados,
        "bytes_remotos_previstos_antes_download": bytes_remotos_previstos,
        "bytes_recebidos": recebidos,
        "bytes_extraidos": bytes_extraidos,
        "arquivos_manifestados_zip": arquivos_manifestados_zip,
        "bytes_manifestados_zip": bytes_manifestados_zip,
        "arquivos_reutilizados_identicos": arquivos_reutilizados,
        "bytes_reutilizados_identicos": bytes_reutilizados,
        "inventario_final": inventario_final,
        "integridade_extracao_confirmada": integridade_extracao_confirmada,
        "por_produto": por_produto,
        "avisos_falha_origem": avisos_origem,
        "auditoria_completa": auditoria_completa,
        "http_totalmente_verificado": http_totalmente_verificado,
        "tamanhos_pre_download_confirmados": tamanhos_pre_download_confirmados,
    }
    estado_execucao["integrity_summary"] = resumo
    log_message("=" * 50)
    log_message("RESUMO DE INTEGRIDADE E TAMANHOS")
    log_message(
        f"Downloads registrados: {len(relatorios_download)}/{total_itens}; "
        f"tamanho HTTP conferido: {verificados_http}; sem tamanho anunciado: {sem_tamanho}."
    )
    log_message(f"Total recebido: {formatar_tamanho_bytes(recebidos)}.")
    log_message(
        f"Total HTTP esperado para respostas com tamanho conhecido: "
        f"{formatar_tamanho_bytes(esperados)}."
    )
    log_message(f"Conteúdo extraído e conferido: {formatar_tamanho_bytes(bytes_extraidos)}.")
    log_message(
        f"Conteúdo total declarado dentro dos ZIPs: {formatar_tamanho_bytes(bytes_manifestados_zip)}; "
        f"arquivos: {arquivos_manifestados_zip}."
    )
    log_message(
        f"Conteúdo idêntico já existente, sem substituição: "
        f"{formatar_tamanho_bytes(bytes_reutilizados)} em {arquivos_reutilizados} arquivo(s)."
    )
    log_message(
        f"Inventário final da pasta: {inventario_final.get('extraidos', 0)} arquivos; "
        f"{formatar_tamanho_bytes(int(inventario_final.get('bytes_extraidos', 0)))}."
    )
    for produto, dados in sorted(por_produto.items()):
        log_message(
            f"Produto {produto}: {dados['zips']} ZIP(s), "
            f"{dados['arquivos_manifestados_zip']} arquivo(s), "
            f"{formatar_tamanho_bytes(dados['bytes_manifestados_zip'])} declarados nos ZIPs; "
            f"{formatar_tamanho_bytes(dados['bytes_gravados'])} gravados e "
            f"{formatar_tamanho_bytes(dados['bytes_reutilizados_identicos'])} já existentes/idênticos."
        )
    if not integridade_extracao_confirmada:
        log_message(
            "A integridade da extração não pôde ser confirmada integralmente. "
            "A execução será marcada como parcial.",
            "error",
        )
    if avisos_origem:
        log_message(
            f"A origem declarou falhas em {avisos_origem} relatório(s). "
            "O resultado não pode ser classificado como integral.",
            "warning",
        )
    elif http_totalmente_verificado:
        log_message("Todos os downloads tiveram o tamanho HTTP integralmente conferido.", "success")
    else:
        log_message(
            "A execução terminou com ressalva de auditoria: nem todos os downloads "
            "possuem tamanho HTTP conhecido e registrado.",
            "warning",
        )
    log_message("=" * 50)
    return resumo


def processar_produtos_incremental(senha, pasta_destino):
    """Baixa, descriptografa e extrai um arquivo por vez para limitar o uso de disco."""
    pasta_temp = estado_execucao["paths"]["temp"]
    pasta_zips = estado_execucao["paths"]["zips"]
    os.makedirs(pasta_temp, exist_ok=True)
    os.makedirs(pasta_zips, exist_ok=True)
    itens_brutos = [(id_tag, link) for id_tag, links in links_armazenados.items() for link in links]
    itens = []
    links_vistos = set()
    for id_tag, link in itens_brutos:
        if link in links_vistos:
            continue
        links_vistos.add(link)
        itens.append((id_tag, link))
    duplicados_manifesto = len(itens_brutos) - len(itens)
    estado_execucao["links_duplicados_no_manifesto"] = duplicados_manifesto
    total = len(itens)
    if not itens:
        log_message("Nenhum produto encontrado para download.", "info")
        salvar_estado_atual("concluido", "concluido")
        return True

    inventario_remoto = inventariar_tamanhos_remotos(itens)
    if inventario_remoto is None:
        return False

    log_message(f"Processando {total} arquivos únicos, um por vez, com retomada automática.")
    if duplicados_manifesto:
        log_message(
            f"O manifesto repetiu {duplicados_manifesto} link(s) idêntico(s); cada URL será "
            "processada uma única vez e a repetição ficará registrada.",
            "warning",
        )
    for indice, (id_tag, link) in enumerate(itens, 1):
        metadados_remotos = estado_execucao.get("remote_inventory", {}).get(link, {})
        metadados_produto = estado_execucao.setdefault("product_metadata", {}).setdefault(
            link,
            {},
        )
        titulo_produto = metadados_produto.get("nome_produto") or metadados_remotos.get(
            "nome_produto"
        )
        pasta_produto_nome = nome_pasta_produto(id_tag, titulo_produto or "")
        nome_gpg = metadados_produto.get("nome_arquivo_original") or metadados_remotos.get(
            "nome_arquivo_original"
        )
        nome_gpg = nome_gpg or nome_arquivo_original_url(link)
        metadados_produto.update(
            {
                "produto": id_tag,
                "nome_produto": titulo_produto or pasta_produto_nome,
                "pasta_produto": pasta_produto_nome,
                "nome_arquivo_original": nome_gpg,
            }
        )
        relatorio_download_existente = estado_execucao.setdefault("download_checks", {}).get(link)
        if isinstance(relatorio_download_existente, dict):
            relatorio_download_existente.setdefault("produto", id_tag)
            relatorio_download_existente.setdefault("nome_produto", pasta_produto_nome)
            relatorio_download_existente.setdefault("nome_arquivo_original", nome_gpg)
        etapa = status_links.get(link, "pendente")
        if etapa == "concluido":
            update_progress(int(indice / total * 100), f"Arquivo {indice}/{total} já concluído")
            continue

        pasta_temp_produto = os.path.join(pasta_temp, pasta_produto_nome)
        pasta_zips_produto = os.path.join(pasta_zips, pasta_produto_nome)
        os.makedirs(pasta_temp_produto, exist_ok=True)
        os.makedirs(pasta_zips_produto, exist_ok=True)
        caminho_gpg = os.path.join(pasta_temp_produto, nome_gpg)
        nome_zip = nome_gpg[:-4] if nome_gpg.lower().endswith(".zip.gpg") else f"{os.path.splitext(nome_gpg)[0]}.zip"
        caminho_zip = os.path.join(pasta_zips_produto, nome_zip)

        # Retomadas iniciadas em versões anteriores continuam aproveitando os
        # temporários antigos, que tinham um nome técnico com hash na pasta raiz.
        nome_legado = nome_download_estavel(id_tag, link)
        caminho_gpg_legado = os.path.join(pasta_temp, nome_legado)
        caminho_zip_legado = os.path.join(pasta_zips, f"{os.path.splitext(nome_legado)[0]}.zip")
        if not (os.path.exists(caminho_gpg) or os.path.exists(f"{caminho_gpg}.part")) and (
            os.path.exists(caminho_gpg_legado) or os.path.exists(f"{caminho_gpg_legado}.part")
        ):
            caminho_gpg = caminho_gpg_legado
        if not os.path.exists(caminho_zip) and os.path.exists(caminho_zip_legado):
            caminho_zip = caminho_zip_legado

        pasta_extracao_produto = os.path.join(pasta_destino, pasta_produto_nome)
        update_progress(
            int((indice - 1) / total * 100),
            f"Processando {pasta_produto_nome} ({indice}/{total})",
        )

        tamanho_remoto = int(metadados_remotos.get("bytes_esperados") or 0)
        if os.path.exists(caminho_gpg) and tamanho_remoto:
            tamanho_local = os.path.getsize(caminho_gpg)
            if tamanho_local != tamanho_remoto:
                registrar_incidente(
                    "INTEGRIDADE_LOCAL",
                    "retomada_download",
                    "SAVED_FILE_SIZE_MISMATCH",
                    f"Temporário salvo possui {tamanho_local} de {tamanho_remoto} bytes.",
                    item=caminho_gpg,
                )
                if tamanho_local < tamanho_remoto and not os.path.exists(f"{caminho_gpg}.part"):
                    os.replace(caminho_gpg, f"{caminho_gpg}.part")
                    etapa = "erro_download"
                    status_links[link] = etapa
                else:
                    status_links[link] = "erro_download"
                    status_downloads[id_tag] = "❌ Temporário com tamanho divergente"
                    salvar_estado_atual("parcial", "produtos")
                    continue
            elif not isinstance(relatorio_download_existente, dict):
                estado_execucao.setdefault("download_checks", {})[link] = {
                    "produto": id_tag,
                    "bytes_esperados": tamanho_remoto,
                    "bytes_esperados_pre_download": tamanho_remoto,
                    "bytes_recebidos": tamanho_local,
                    "tamanho_http_verificado": True,
                    "sha256_arquivo_recebido": calcular_sha256(caminho_gpg),
                    "recuperado_de_temporario_verificado": True,
                }

        if os.path.exists(caminho_zip):
            etapa = "pronto_extracao"
            status_links[link] = etapa
        elif etapa == "pronto_extracao":
            etapa = "pendente"
        if etapa in ("pendente", "erro_download") and not os.path.exists(caminho_gpg):
            log_message(f"Baixando produto {id_tag} ({indice}/{total})")
            relatorio_download = {}
            download_ok = baixar_arquivo(
                link,
                caminho_gpg,
                relatorio=relatorio_download,
                tamanho_aferido=metadados_remotos.get("bytes_esperados"),
                etag_aferido=metadados_remotos.get("etag"),
            )
            relatorio_download["produto"] = id_tag
            relatorio_download["nome_produto"] = pasta_produto_nome
            relatorio_download["nome_arquivo_original"] = nome_gpg
            estado_execucao.setdefault("download_checks", {})[link] = relatorio_download
            if not download_ok:
                status_links[link] = "erro_download"
                status_downloads[id_tag] = "❌ Erro no download"
                salvar_estado_atual("parcial", "produtos")
                atualizar_lista()
                continue
            historico_downloads.add(link)
            status_links[link] = "baixado"
            status_downloads[id_tag] = "✅ Download concluído"
            salvar_estado_atual("em_andamento", "produtos")

        if not os.path.exists(caminho_zip):
            if not os.path.exists(caminho_gpg):
                status_links[link] = "erro_download"
                salvar_estado_atual("parcial", "produtos")
                continue
            status_downloads[id_tag] = "🔐 Descriptografando"
            atualizar_lista()
            if not descriptografar_arquivo(caminho_gpg, senha, caminho_zip):
                status_links[link] = "erro_descriptografia"
                status_downloads[id_tag] = "❌ Erro na descriptografia"
                salvar_estado_atual("parcial", "produtos")
                atualizar_lista()
                continue
            status_links[link] = "pronto_extracao"
            salvar_estado_atual("em_andamento", "produtos")
            try:
                os.remove(caminho_gpg)
            except OSError as erro:
                log_message(f"Não foi possível remover o temporário GPG: {erro}", "warning")

        status_downloads[id_tag] = "📂 Extraindo"
        atualizar_lista()
        relatorio_extracao = {}
        if extrair_um_zip(caminho_zip, pasta_extracao_produto, relatorio=relatorio_extracao, permitir_recuperacao=True):
            relatorio_extracao["produto"] = id_tag
            relatorio_extracao["nome_produto"] = pasta_produto_nome
            relatorio_extracao["pasta_produto"] = pasta_extracao_produto
            relatorio_extracao["nome_arquivo_original"] = nome_gpg
            estado_execucao.setdefault("extraction_checks", {})[link] = relatorio_extracao
            status_links[link] = "concluido"
            status_downloads[id_tag] = "✅ Concluído"
            if not relatorio_extracao.get("recuperacao_isolada"):
                try:
                    os.remove(caminho_zip)
                except OSError as erro:
                    log_message(f"Não foi possível remover o ZIP já extraído: {erro}", "warning")
        else:
            status_links[link] = "erro_extracao"
            status_downloads[id_tag] = "❌ Erro na extração"
        salvar_estado_atual("em_andamento", "produtos")
        atualizar_lista()
        update_progress(int(indice / total * 100), f"Processados {indice}/{total} arquivos")

    concluiu = all(status_links.get(link) == "concluido" for _, link in itens)
    update_progress(99, "Conferindo todos os arquivos da pasta final...")
    log_message("Executando a checagem final completa da pasta extraída.")
    resumo_integridade = registrar_resumo_integridade(total)
    concluiu = concluiu and bool(resumo_integridade.get("integridade_extracao_confirmada"))
    salvar_estado_atual("concluido" if concluiu else "parcial", "concluido" if concluiu else "produtos")
    return concluiu


def executar_execucao(senha):
    pasta_destino = preparar_principal_e_links(senha)
    if not pasta_destino:
        return False
    return processar_produtos_incremental(senha, pasta_destino)


def definir_execucao_em_andamento(em_andamento):
    global execucao_em_andamento
    if threading.current_thread() is not threading.main_thread():
        executar_na_interface(definir_execucao_em_andamento, em_andamento)
        return

    execucao_em_andamento = em_andamento
    if em_andamento:
        btn_processar.config(state=tk.DISABLED)
        btn_retomar.config(state=tk.DISABLED)
        btn_auditar.config(state=tk.DISABLED)
    else:
        validate_input()
        btn_retomar.config(state=tk.NORMAL)
        btn_auditar.config(state=tk.NORMAL)


def iniciar_processamento():
    url = entrada_url.get().strip()
    senha = entrada_senha.get().strip()
    analista = entrada_analista.get().strip()

    if not url or not senha or not analista:
        show_error("⚠️ Preencha todos os campos antes de continuar!")
        return
    if not validar_url_https(url):
        show_error("Informe uma URL HTTPS válida, sem usuário ou senha embutidos.")
        return
    if not localizar_gpg():
        show_error("GPG não encontrado. Instale o Gpg4win ou coloque gpg.exe em gpg\\bin ao lado do Nêmesis.")
        return

    definir_execucao_em_andamento(True)
    threading.Thread(
        target=processar,
        args=(url, senha, analista),
        daemon=True,
        name="nemesis-processamento",
    ).start()


def processar(url, senha, analista):
    global estado_execucao, caminho_estado_atual, caminho_log_atual, links_armazenados

    concluiu = False
    erro_fatal = None
    try:
        caminho_log_atual = None
        limpar_historico()
        pasta_analista = pasta_saida_segura(pasta_base_execucao(), analista)
        pasta_execucao = criar_diretorio_execucao(pasta_analista, url)
        caminhos = {
            "root": str(pasta_execucao),
            "temp": str(pasta_execucao / "temp"),
            "zips": str(pasta_execucao / "zips"),
            "contas": str(pasta_execucao / "contas"),
        }
        for caminho in caminhos.values():
            os.makedirs(caminho, exist_ok=True)
        caminho_estado_atual = str(pasta_execucao / ARQUIVO_ESTADO)
        caminho_log_atual = str(pasta_execucao / ARQUIVO_LOG)
        agora = datetime.now().isoformat(timespec="seconds")
        estado_execucao = {
            "schema_version": 1,
            "app_version": APP_VERSION,
            "analyst": analista,
            "source_url": url,
            "created_at": agora,
            "updated_at": agora,
            "status": "em_andamento",
            "fase": "principal",
            "paths": caminhos,
            "accounts": {},
            "products": {},
            "link_status": {},
            "download_checks": {},
            "extraction_checks": {},
            "product_metadata": {},
            "incidentes": [],
        }
        salvar_estado_atual()
        log_message("=" * 50)
        log_message(f"INICIANDO PROCESSAMENTO PARA ANALISTA: {analista}")
        log_message(f"Execução isolada em: {pasta_execucao}")
        log_message("=" * 50)
        concluiu = executar_execucao(senha)
        if concluiu:
            resumo = estado_execucao.get("integrity_summary", {})
            if resumo.get("avisos_falha_origem"):
                log_message(
                    "PROCESSAMENTO CONCLUÍDO COM FALHAS DECLARADAS PELA ORIGEM APPLE.",
                    "warning",
                )
            elif resumo.get("http_totalmente_verificado"):
                log_message("PROCESSAMENTO CONCLUÍDO COM SUCESSO E TAMANHOS CONFERIDOS.", "success")
            else:
                log_message(
                    "PROCESSAMENTO CONCLUÍDO COM RESSALVA: consulte o resumo de integridade.",
                    "warning",
                )
        else:
            log_message("Processamento parcial. Use 'Retomar execução' para tentar os itens pendentes.", "warning")
        salvar_estado_atual()

    except Exception as e:
        erro_fatal = f"{type(e).__name__}: {e}"
        registrar_incidente(
            "SISTEMA_NEMESIS",
            "execucao",
            type(e).__name__,
            "Ocorreu uma exceção interna não prevista pelo fluxo normal.",
        )
        salvar_estado_atual("parcial")
        log_message("\n" + "="*50)
        log_message(f"❌ ERRO INESPERADO: {str(e)}", "error")
        log_message(traceback.format_exc(), "error")
        log_message("="*50)
    finally:
        atualizar_lista()
        update_progress(100, "Processo finalizado")
        definir_execucao_em_andamento(False)
        mensagem_final = montar_resumo_final(estado_execucao, concluiu, erro_fatal)
        resumo_final = estado_execucao.get("integrity_summary", {})
        sucesso_integral = bool(
            concluiu
            and estado_execucao.get("remote_inventory_summary", {}).get("completo")
            and resumo_final.get("http_totalmente_verificado")
            and resumo_final.get("integridade_extracao_confirmada")
            and not resumo_final.get("avisos_falha_origem")
            and not erro_fatal
        )
        mostrar_resumo_final_e_fechar(mensagem_final, sucesso_integral)


def iniciar_auditoria_existente():
    """Inicia uma auditoria somente leitura da extração mais recente."""
    analista = entrada_analista.get().strip()
    if not analista:
        show_error("Informe o mesmo nome de analista usado na execução que deseja auditar.")
        return
    definir_execucao_em_andamento(True)
    threading.Thread(
        target=auditar_ultima_execucao,
        args=(analista,),
        daemon=True,
        name="nemesis-auditoria",
    ).start()


def auditar_ultima_execucao(analista):
    """Gera evidências para uma execução antiga sem alterar seu estado ou log."""
    try:
        pasta_analista = pasta_saida_segura(pasta_base_execucao(), analista)
        caminho_estado = localizar_estado_mais_recente(pasta_analista)
        if not caminho_estado:
            show_info("Nenhuma execução foi encontrada para este analista.")
            return
        estado_original = carregar_estado(caminho_estado)
        pasta_contas = estado_original.get("paths", {}).get("contas")
        if not pasta_contas or not os.path.isdir(pasta_contas):
            show_error("A pasta extraída da execução não foi encontrada.")
            return

        update_progress(0, "Auditando arquivos extraídos...")
        log_message(
            f"Auditando a execução {Path(caminho_estado).parent.name} sem alterar o estado original."
        )
        inventario = auditar_pasta_extraida(pasta_contas)
        estado_auditoria = copy.deepcopy(estado_original)
        estado_auditoria["audit_app_version"] = APP_VERSION
        estado_auditoria.setdefault("extraction_checks", {})["auditoria_pos_execucao"] = inventario
        estado_auditoria.setdefault("incidentes", [])
        for erro_leitura in inventario.get("erros_leitura", []):
            estado_auditoria["incidentes"].append(
                {
                    "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "categoria": "AMBIENTE_SERVIDOR",
                    "etapa": "auditoria_pos_execucao",
                    "codigo": erro_leitura.get("erro"),
                    "mensagem": "Um arquivo não pôde ser lido durante a auditoria posterior.",
                }
            )
        resumo = estado_auditoria.setdefault("integrity_summary", {})
        resumo["bytes_extraidos_auditados"] = inventario.get("bytes_extraidos", 0)
        resumo["arquivos_extraidos_auditados"] = inventario.get("extraidos", 0)
        resumo["avisos_falha_origem"] = len(inventario.get("avisos_origem", []))
        caminho_log = Path(caminho_estado).parent / ARQUIVO_LOG
        relatorio = gerar_relatorios_auditoria(estado_auditoria, caminho_log)
        caminho_relatorio = Path(caminho_estado).parent / "relatorio_auditoria.txt"
        quantidade_avisos = len(relatorio.get("avisos_de_falha_na_origem", []))
        update_progress(100, "Auditoria concluída")
        if quantidade_avisos:
            show_warning(
                f"Auditoria concluída. Foram encontrados {quantidade_avisos} relatório(s) "
                f"de falha declarada pela origem Apple. Evidência: {caminho_relatorio}"
            )
        else:
            show_info(f"Auditoria concluída. Relatório: {caminho_relatorio}")
    except Exception as erro:
        log_message(f"Falha na auditoria posterior: {type(erro).__name__}: {erro}", "error")
        show_error("Não foi possível concluir a auditoria. Consulte o log exibido na tela.")
    finally:
        definir_execucao_em_andamento(False)


def iniciar_retomada():
    senha = entrada_senha.get().strip()
    analista = entrada_analista.get().strip()
    if not senha or not analista:
        show_error("Preencha todos os campos antes de continuar!")
        return
    if not localizar_gpg():
        show_error("GPG não encontrado. Instale o Gpg4win ou coloque gpg.exe em gpg\\bin ao lado do Nêmesis.")
        return

    definir_execucao_em_andamento(True)
    threading.Thread(
        target=retomar_downloads,
        args=(senha, analista),
        daemon=True,
        name="nemesis-retomada",
    ).start()


def retomar_downloads(senha, analista):
    concluiu = False
    erro_fatal = None
    execucao_carregada = False
    try:
        pasta_analista = pasta_saida_segura(pasta_base_execucao(), analista)
        caminho_estado = localizar_estado_pendente(pasta_analista)
        if not caminho_estado:
            show_info("Nenhuma execução pendente foi encontrada para este analista.")
            return
        carregar_execucao(caminho_estado)
        execucao_carregada = True
        executar_na_interface(limpar_interface)
        atualizar_lista()
        log_message("=" * 50)
        log_message(f"RETOMANDO EXECUÇÃO SALVA: {Path(caminho_estado).parent.name}")
        log_message("=" * 50)
        concluiu = executar_execucao(senha)
        if concluiu:
            resumo = estado_execucao.get("integrity_summary", {})
            if resumo.get("avisos_falha_origem"):
                log_message(
                    "RETOMADA CONCLUÍDA COM FALHAS DECLARADAS PELA ORIGEM APPLE.",
                    "warning",
                )
            elif resumo.get("http_totalmente_verificado"):
                log_message("RETOMADA CONCLUÍDA COM SUCESSO E TAMANHOS CONFERIDOS.", "success")
            else:
                log_message(
                    "RETOMADA CONCLUÍDA COM RESSALVA: consulte o resumo de integridade.",
                    "warning",
                )
        else:
            log_message("A retomada terminou com itens pendentes. Os dados foram preservados.", "warning")
        salvar_estado_atual()
    except Exception as e:
        erro_fatal = f"{type(e).__name__}: {e}"
        registrar_incidente(
            "SISTEMA_NEMESIS",
            "retomada",
            type(e).__name__,
            "Ocorreu uma exceção interna não prevista durante a retomada.",
        )
        salvar_estado_atual("parcial")
        log_message(f"Erro inesperado ao retomar downloads: {str(e)}", "error")
        log_message(traceback.format_exc(), "error")
    finally:
        atualizar_lista()
        update_progress(100, "Retomada finalizada")
        definir_execucao_em_andamento(False)
        if execucao_carregada:
            mensagem_final = montar_resumo_final(estado_execucao, concluiu, erro_fatal)
            resumo_final = estado_execucao.get("integrity_summary", {})
            sucesso_integral = bool(
                concluiu
                and estado_execucao.get("remote_inventory_summary", {}).get("completo")
                and resumo_final.get("http_totalmente_verificado")
                and resumo_final.get("integridade_extracao_confirmada")
                and not resumo_final.get("avisos_falha_origem")
                and not erro_fatal
            )
            mostrar_resumo_final_e_fechar(mensagem_final, sucesso_integral)


def ao_fechar():
    if execucao_em_andamento:
        fechar = messagebox.askyesno(
            "Processamento em andamento",
            "Há uma execução em andamento. Se fechar agora, ela será interrompida, mas poderá ser "
            "continuada depois pelo botão 'Retomar execução'. Deseja fechar?",
        )
        if not fechar:
            return
    app.destroy()

# Inicialização da aplicação
if __name__ == "__main__":
    app.protocol("WM_DELETE_WINDOW", ao_fechar)
    app.after(50, processar_fila_interface)
    log_message("Aplicação Nêmesis inicializada")
    app.mainloop()
