import csv
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta


PASTA = os.path.dirname(os.path.abspath(__file__))


def conectar(caminho=os.path.join(PASTA, "estoque.db")):
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS produto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL UNIQUE,
            categoria TEXT NOT NULL DEFAULT 'Geral',
            quantidade INTEGER NOT NULL DEFAULT 0 CHECK (quantidade >= 0),
            minimo INTEGER NOT NULL DEFAULT 0 CHECK (minimo >= 0),
            preco_custo REAL NOT NULL CHECK (preco_custo >= 0),
            preco_venda REAL NOT NULL CHECK (preco_venda >= 0),
            validade TEXT
        );
        CREATE TABLE IF NOT EXISTS movimentacao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produto_id INTEGER NOT NULL REFERENCES produto(id),
            tipo TEXT NOT NULL CHECK (tipo IN ('ENTRADA', 'SAIDA')),
            quantidade INTEGER NOT NULL CHECK (quantidade > 0),
            data TEXT NOT NULL
        );
    """)
    return con


def cadastrar_produto(con, nome, categoria, quantidade, minimo,
                      preco_custo, preco_venda, validade=None):
    nome = nome.strip()
    if nome == "":
        raise ValueError("O nome do produto é obrigatório.")
    if validade:
        try:
            validade = datetime.strptime(validade, "%d/%m/%Y").date().isoformat()
        except ValueError:
            raise ValueError("Data de validade inválida. Use dd/mm/aaaa.")
    categoria = categoria.strip() or "Geral"
    try:
        cur = con.execute(
            "INSERT INTO produto (nome, categoria, quantidade, minimo, "
            "preco_custo, preco_venda, validade) VALUES (?,?,?,?,?,?,?)",
            (nome, categoria, quantidade, minimo, preco_custo,
             preco_venda, validade))
    except sqlite3.IntegrityError:
        raise ValueError("Produto já cadastrado ou com valor inválido.")
    con.commit()
    return cur.lastrowid


def listar_produtos(con, busca=""):
    return con.execute(
        "SELECT * FROM produto WHERE nome LIKE ? ORDER BY nome COLLATE NOCASE",
        ("%" + busca.strip() + "%",)).fetchall()


def buscar_produto(con, produto_id):
    return con.execute("SELECT * FROM produto WHERE id = ?",
                       (produto_id,)).fetchone()


def movimentar(con, produto_id, tipo, quantidade, data=None):
    tipo = tipo.upper()
    if tipo not in ("ENTRADA", "SAIDA"):
        raise ValueError("Tipo deve ser ENTRADA ou SAIDA.")
    if quantidade <= 0:
        raise ValueError("A quantidade deve ser maior que zero.")
    produto = buscar_produto(con, produto_id)
    if produto is None:
        raise ValueError("Produto não encontrado.")
    if tipo == "ENTRADA":
        delta = quantidade
    else:
        delta = -quantidade
    if produto["quantidade"] + delta < 0:
        raise ValueError("Estoque insuficiente: há apenas "
                         f"{produto['quantidade']} unidade(s).")
    if data is None:
        data = date.today().isoformat()
    con.execute("UPDATE produto SET quantidade = quantidade + ? WHERE id = ?",
                (delta, produto_id))
    con.execute(
        "INSERT INTO movimentacao (produto_id, tipo, quantidade, data) "
        "VALUES (?,?,?,?)", (produto_id, tipo, quantidade, data))
    con.commit()


def produtos_para_repor(con):
    return con.execute(
        "SELECT * FROM produto WHERE quantidade <= minimo "
        "ORDER BY (quantidade - minimo), nome").fetchall()


def produtos_vencendo(con, dias=7, hoje=None):
    if hoje is None:
        hoje = date.today()
    limite = (hoje + timedelta(days=dias)).isoformat()
    return con.execute(
        "SELECT * FROM produto WHERE validade IS NOT NULL AND validade <= ? "
        "AND quantidade > 0 ORDER BY validade", (limite,)).fetchall()


def resumo_financeiro(con):
    linha = con.execute(
        "SELECT COALESCE(SUM(quantidade * preco_custo), 0) AS custo, "
        "COALESCE(SUM(quantidade * preco_venda), 0) AS venda, "
        "COUNT(*) AS itens FROM produto").fetchone()
    custo = linha["custo"]
    venda = linha["venda"]
    margem = 0.0
    if venda > 0:
        margem = (venda - custo) / venda * 100
    return {"itens": linha["itens"], "custo": custo,
            "venda": venda, "margem": margem}


def historico(con, produto_id):
    return con.execute(
        "SELECT * FROM movimentacao WHERE produto_id = ? ORDER BY id DESC",
        (produto_id,)).fetchall()


def moeda(valor):
    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return "R$ " + texto


def formatar_data(iso):
    if not iso:
        return "-"
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")


def tabela_produtos(produtos):
    if len(produtos) == 0:
        return "Nenhum produto encontrado."
    linhas = [f"{'ID':>3}  {'Produto':<24}{'Categoria':<14}{'Qtd':>5}"
              f"{'Mín':>5}{'Venda':>12}  {'Validade':<10}", "-" * 79]
    for p in produtos:
        linhas.append(
            f"{p['id']:>3}  {p['nome'][:23]:<24}{p['categoria'][:13]:<14}"
            f"{p['quantidade']:>5}{p['minimo']:>5}"
            f"{moeda(p['preco_venda']):>12}  {formatar_data(p['validade']):<10}")
    return "\n".join(linhas)


def relatorio_reposicao(con):
    itens = produtos_para_repor(con)
    if len(itens) == 0:
        return "Nenhum produto abaixo do estoque mínimo."
    linhas = ["PRODUTOS PARA REPOR", "-" * 52]
    for p in itens:
        comprar = max(p["minimo"] * 2 - p["quantidade"], 1)
        linhas.append(f"{p['nome'][:24]:<25} estoque {p['quantidade']:>3} / "
                      f"mín. {p['minimo']:>3}  -> comprar {comprar}")
    return "\n".join(linhas)


def relatorio_validade(con, dias=7, hoje=None):
    if hoje is None:
        hoje = date.today()
    itens = produtos_vencendo(con, dias, hoje)
    if len(itens) == 0:
        return f"Nenhum produto vencido ou vencendo em {dias} dias."
    linhas = [f"VALIDADE (próximos {dias} dias)", "-" * 52]
    for p in itens:
        vencimento = date.fromisoformat(p["validade"])
        restam = (vencimento - hoje).days
        if restam < 0:
            situacao = "VENCIDO"
        else:
            situacao = f"vence em {restam} dia(s)"
        linhas.append(f"{p['nome'][:24]:<25} "
                      f"{formatar_data(p['validade'])}  {situacao}")
    return "\n".join(linhas)


def relatorio_financeiro(con):
    r = resumo_financeiro(con)
    linhas = ["RESUMO FINANCEIRO DO ESTOQUE", "-" * 52,
              f"Produtos cadastrados  : {r['itens']}",
              f"Valor a preço de custo: {moeda(r['custo'])}",
              f"Valor a preço de venda: {moeda(r['venda'])}",
              f"Margem potencial      : {r['margem']:.1f}%"]
    return "\n".join(linhas)


def exportar_csv(con, caminho):
    produtos = listar_produtos(con)
    with open(caminho, "w", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(["id", "produto", "categoria", "quantidade",
                           "minimo", "preco_custo", "preco_venda", "validade"])
        for p in produtos:
            escritor.writerow([p["id"], p["nome"], p["categoria"],
                               p["quantidade"], p["minimo"], p["preco_custo"],
                               p["preco_venda"], formatar_data(p["validade"])])
    return len(produtos)


MENU = """
========== ESTOQUEFÁCIL ==========
1 - Cadastrar produto
2 - Listar / buscar produtos
3 - Registrar entrada (compra)
4 - Registrar saída (venda)
5 - Alertas de reposição
6 - Alertas de validade
7 - Resumo financeiro
8 - Exportar estoque para CSV
9 - Histórico de um produto
0 - Sair
"""


def ler_inteiro(mensagem, minimo=0):
    while True:
        try:
            valor = int(input(mensagem).strip())
            if valor < minimo:
                print(f"  Digite um número inteiro maior ou igual a {minimo}.")
                continue
            return valor
        except ValueError:
            print("  Valor inválido. Digite um número inteiro.")


def ler_decimal(mensagem):
    while True:
        try:
            valor = float(input(mensagem).strip().replace(",", "."))
            if valor < 0:
                print("  O valor não pode ser negativo.")
                continue
            return valor
        except ValueError:
            print("  Valor inválido. Exemplo: 12,50")


def cadastrar(con):
    nome = input("Nome do produto: ")
    categoria = input("Categoria (Enter = Geral): ")
    quantidade = ler_inteiro("Quantidade inicial: ")
    minimo = ler_inteiro("Estoque mínimo: ")
    custo = ler_decimal("Preço de custo (R$): ")
    venda = ler_decimal("Preço de venda (R$): ")
    validade = input("Validade dd/mm/aaaa (Enter = sem validade): ").strip()
    try:
        novo_id = cadastrar_produto(con, nome, categoria, quantidade,
                                          minimo, custo, venda, validade)
        print(f"Produto cadastrado com o ID {novo_id}.")
    except ValueError as erro:
        print(f"Erro: {erro}")


def registrar_movimentacao(con, tipo):
    print(tabela_produtos(listar_produtos(con)))
    produto_id = ler_inteiro("ID do produto: ", 1)
    quantidade = ler_inteiro("Quantidade: ", 1)
    try:
        movimentar(con, produto_id, tipo, quantidade)
        produto = buscar_produto(con, produto_id)
        if tipo == "ENTRADA":
            nome_tipo = "Entrada"
        else:
            nome_tipo = "Saída"
        print(f"{nome_tipo} registrada. Estoque atual de "
              f"{produto['nome']}: {produto['quantidade']}.")
    except ValueError as erro:
        print(f"Erro: {erro}")


def mostrar_historico(con):
    produto_id = ler_inteiro("ID do produto: ", 1)
    produto = buscar_produto(con, produto_id)
    if produto is None:
        print("Produto não encontrado.")
        return
    print(f"Histórico de {produto['nome']}:")
    movimentacoes = historico(con, produto_id)
    if len(movimentacoes) == 0:
        print("  Sem movimentações.")
    for m in movimentacoes:
        data = formatar_data(m["data"])
        print(f"  {data}  {m['tipo']:<7} {m['quantidade']:>4}")


def data_daqui(dias):
    return (date.today() + timedelta(days=dias)).strftime("%d/%m/%Y")


def carregar_demo(con):
    exemplos = [
        ("Arroz 5kg", "Mercearia", 24, 10, 22.50, 29.90, data_daqui(300)),
        ("Feijão carioca 1kg", "Mercearia", 6, 12, 6.80, 9.50, data_daqui(200)),
        ("Leite integral 1L", "Laticínios", 18, 20, 4.10, 5.99, data_daqui(4)),
        ("Iogurte natural", "Laticínios", 9, 8, 2.20, 3.80, data_daqui(-1)),
        ("Pão de forma", "Padaria", 5, 6, 5.40, 8.90, data_daqui(2)),
        ("Refrigerante 2L", "Bebidas", 30, 12, 6.20, 9.99, data_daqui(120)),
        ("Detergente", "Limpeza", 15, 8, 1.90, 3.49, None),
        ("Café 500g", "Mercearia", 3, 8, 14.00, 21.90, data_daqui(180)),
    ]
    for produto in exemplos:
        try:
            cadastrar_produto(con, *produto)
        except ValueError:
            pass
    print("Dados de exemplo carregados.")


def main():
    con = conectar()
    if "--demo" in sys.argv:
        carregar_demo(con)

    while True:
        print(MENU)
        try:
            opcao = input("Escolha uma opção: ").strip()
        except EOFError:
            break

        if opcao == "1":
            cadastrar(con)
        elif opcao == "2":
            busca = input("Buscar por nome (Enter = todos): ")
            print(tabela_produtos(listar_produtos(con, busca)))
        elif opcao == "3":
            registrar_movimentacao(con, "ENTRADA")
        elif opcao == "4":
            registrar_movimentacao(con, "SAIDA")
        elif opcao == "5":
            print(relatorio_reposicao(con))
        elif opcao == "6":
            print(relatorio_validade(con))
        elif opcao == "7":
            print(relatorio_financeiro(con))
        elif opcao == "8":
            total = exportar_csv(con, os.path.join(PASTA, "estoque.csv"))
            print(f"{total} produtos exportados para estoque.csv")
        elif opcao == "9":
            mostrar_historico(con)
        elif opcao == "0":
            print("Até logo!")
            break
        else:
            print("Opção inválida.")

    con.close()


if __name__ == "__main__":
    main()
