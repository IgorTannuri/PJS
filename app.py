# app.py

import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
from functools import wraps
from database import Database, hash_senha
from werkzeug.utils import secure_filename

# --- CONFIGURAÇÃO DA APLICAÇÃO ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_super_segura_aqui'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


# Função para verificar se a extensão do arquivo é permitida
def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.after_request
def add_ngrok_header(response):
    response.headers.add('ngrok-skip-browser-warning', 'true')
    return response


# --- CONEXÃO COM O BANCO DE DADOS ---
try:
    db = Database()
except Exception as e:
    print(f"FATAL: Não foi possível conectar ao banco de dados. Encerrando. Erro: {e}")
    exit()


# --- ROTA PARA SERVIR IMAGENS ---
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# --- DECORATORS E ROTAS DE AUTENTICAÇÃO ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash("Por favor, faça login para acessar esta página.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('nivel_acesso') != 'administrador':
            flash("Você não tem permissão para acessar esta página.", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)

    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'usuario_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        password_hash = hash_senha(password)
        user = db.executar("SELECT * FROM usuarios WHERE username = %s AND password_hash = %s",
                           (username, password_hash), fetch='one')
        if user:
            session['usuario_id'] = user['id']
            session['username'] = user['username']
            session['nivel_acesso'] = user['nivel_acesso']
            db.registrar_auditoria(user['id'], 'Login', f"Usuário '{username}' logado.")
            flash(f"Bem-vindo, {user['username']}!", "success")
            return redirect(url_for('index'))
        else:
            flash("Usuário ou senha inválidos.", "danger")
    return render_template('login.html')


@app.route('/registrar', methods=['GET', 'POST'])
def registrar():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if not username or not password:
            flash("Usuário e senha são obrigatórios.", "warning")
            return redirect(url_for('registrar'))
        user_exists = db.executar("SELECT id FROM usuarios WHERE username = %s", (username,), fetch='one')
        if user_exists:
            flash("Este nome de usuário já está em uso.", "danger")
            return redirect(url_for('registrar'))

        total_users = db.executar("SELECT COUNT(*) as count FROM usuarios", fetch='one')['count']
        nivel_acesso = 'administrador' if total_users == 0 else 'operador'
        password_hash = hash_senha(password)
        db.executar("INSERT INTO usuarios (username, password_hash, nivel_acesso) VALUES (%s, %s, %s)",
                    (username, password_hash, nivel_acesso))
        flash("Registro realizado com sucesso! Faça o login.", "success")
        return redirect(url_for('login'))
    return render_template('registrar.html')


@app.route('/logout')
def logout():
    if 'usuario_id' in session:
        db.registrar_auditoria(session['usuario_id'], 'Logout', f"Usuário '{session['username']}' deslogado.")
    session.clear()
    flash("Você foi desconectado.", "info")
    return redirect(url_for('login'))


# --- ROTAS DE BEBIDAS ---
@app.route('/')
@login_required
def index():
    user_id = session['usuario_id']
    termo_busca = request.args.get('busca', '')
    categoria_id_filtro = request.args.get('categoria', '')

    query = """
        SELECT b.id, b.codigo, b.nome, COALESCE(c.nome, 'Sem Categoria') as categoria,
               b.quantidade, b.preco_venda, b.quantidade_minima, b.imagem_url
        FROM bebidas b 
        LEFT JOIN categorias c ON b.categoria_id = c.id
        WHERE b.usuario_id = %s AND (LOWER(b.nome) LIKE %s OR LOWER(b.codigo) LIKE %s)
    """
    params = [user_id, f'%{termo_busca.lower()}%', f'%{termo_busca.lower()}%']

    # Adiciona o filtro de categoria se um foi selecionado
    if categoria_id_filtro:
        query += " AND b.categoria_id = %s"
        params.append(int(categoria_id_filtro))

    query += " ORDER BY b.nome"

    bebidas = db.executar(query, tuple(params), fetch='all')

    # Busca todas as categorias do usuário para popular o menu de filtro
    categorias = db.executar("SELECT * FROM categorias WHERE usuario_id = %s ORDER BY nome", (user_id,), fetch='all')

    # Busca alertas de estoque baixo
    alertas = db.executar("SELECT nome FROM bebidas WHERE quantidade <= quantidade_minima AND quantidade_minima > 0 AND usuario_id = %s", (user_id,), fetch='all')
    if alertas:
        nomes_bebidas = ", ".join([a['nome'] for a in alertas])
        flash(f"Alerta de Estoque Baixo! Repor: {nomes_bebidas}", "warning")

    return render_template('index.html', bebidas=bebidas, categorias=categorias,
                           termo_busca=termo_busca, categoria_filtro=categoria_id_filtro)


@app.route('/bebida/adicionar', methods=['GET', 'POST'])
@login_required
def adicionar_bebida():
    user_id = session['usuario_id']
    if request.method == 'POST':
        imagem_url = None
        if 'imagem' in request.files:
            file = request.files['imagem']
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                imagem_url = filename

        try:
            db.executar("""
                INSERT INTO bebidas (codigo, nome, categoria_id, preco_custo, preco_venda, quantidade,
                                     quantidade_minima, imagem_url, usuario_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    request.form['codigo'], request.form['nome'], request.form.get('categoria_id') or None,
                    request.form['preco_custo'], request.form['preco_venda'], request.form['quantidade'],
                    request.form['quantidade_minima'], imagem_url, user_id
                ))
            flash("Bebida adicionada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao adicionar bebida: {e}", "danger")
        return redirect(url_for('index'))

    categorias = db.executar("SELECT * FROM categorias WHERE usuario_id = %s ORDER BY nome", (user_id,), fetch='all')
    return render_template('gerenciar_bebida.html', titulo="Adicionar Bebida", categorias=categorias, bebida=None)


@app.route('/bebida/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_bebida(id):
    user_id = session['usuario_id']
    bebida = db.executar("SELECT * FROM bebidas WHERE id = %s AND usuario_id = %s", (id, user_id), fetch='one')
    if not bebida:
        flash("Bebida não encontrada.", "danger")
        return redirect(url_for('index'))

    if request.method == 'POST':
        imagem_url = bebida['imagem_url']
        if 'imagem' in request.files:
            file = request.files['imagem']
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                imagem_url = filename

        try:
            db.executar("""
                UPDATE bebidas
                SET codigo=%s,nome=%s,categoria_id=%s,preco_custo=%s,preco_venda=%s,
                    quantidade=%s,quantidade_minima=%s,imagem_url=%s
                WHERE id = %s AND usuario_id = %s
                """, (
                    request.form['codigo'], request.form['nome'], request.form.get('categoria_id') or None,
                    request.form['preco_custo'], request.form['preco_venda'], request.form['quantidade'],
                    request.form['quantidade_minima'], imagem_url, id, user_id
                ))
            flash("Bebida atualizada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao atualizar bebida: {e}", "danger")
        return redirect(url_for('index'))

    categorias = db.executar("SELECT * FROM categorias WHERE usuario_id = %s ORDER BY nome", (user_id,), fetch='all')
    return render_template('gerenciar_bebida.html', titulo="Editar Bebida", categorias=categorias, bebida=bebida)


@app.route('/bebida/excluir/<int:id>', methods=['POST'])
@login_required
def excluir_bebida(id):
    user_id = session['usuario_id']
    try:
        res = db.executar("DELETE FROM bebidas WHERE id = %s AND usuario_id = %s", (id, user_id))
        if res > 0:
            flash("Bebida excluída com sucesso.", "success")
            db.registrar_auditoria(user_id, 'Excluir Bebida', f"Bebida ID: {id}")
        else:
            flash("Bebida não encontrada ou você não tem permissão para excluí-la.", "danger")
    except Exception as e:
        flash(f"Erro ao excluir bebida: {e}", "danger")
    return redirect(url_for('index'))


@app.route('/categorias', methods=['GET', 'POST'])
@login_required
def gerenciar_categorias():
    user_id = session['usuario_id']
    if request.method == 'POST':
        nome = request.form.get('nome')
        try:
            if nome:
                db.executar("INSERT INTO categorias (nome, usuario_id) VALUES (%s, %s)", (nome, user_id))
                flash("Categoria adicionada.", "success")
                db.registrar_auditoria(user_id, 'Adicionar Categoria', f"Categoria '{nome}'")
        except Exception as e:
            flash(f"Erro ao adicionar categoria: {e}", "danger")
        return redirect(url_for('gerenciar_categorias'))

    categorias = db.executar("SELECT * FROM categorias WHERE usuario_id = %s ORDER BY nome", (user_id,), fetch='all')
    return render_template('gerenciar_categorias.html', categorias=categorias)


@app.route('/categoria/excluir/<int:id>', methods=['POST'])
@login_required
def excluir_categoria(id):
    user_id = session['usuario_id']
    try:
        res = db.executar("DELETE FROM categorias WHERE id = %s AND usuario_id = %s", (id, user_id))
        if res > 0:
            flash("Categoria excluída.", "success")
            db.registrar_auditoria(user_id, 'Excluir Categoria', f"Categoria ID: {id}")
        else:
            flash("Categoria não encontrada ou você não tem permissão.", "danger")
    except Exception as e:
        flash(f"Erro ao excluir categoria: {e}", "danger")
    return redirect(url_for('gerenciar_categorias'))


@app.route('/fornecedores', methods=['GET', 'POST'])
@login_required
def gerenciar_fornecedores():
    user_id = session['usuario_id']
    if request.method == 'POST':
        dados = (
            request.form.get('nome'), request.form.get('contato'),
            request.form.get('endereco'), request.form.get('cnpj'),
            request.form.get('email'),
            user_id
        )
        try:
            db.executar(
                "INSERT INTO fornecedores (nome, contato, endereco, cnpj, email, usuario_id) VALUES (%s, %s, %s, %s, %s, %s)",
                dados)
            flash("Fornecedor adicionado.", "success")
            db.registrar_auditoria(user_id, 'Adicionar Fornecedor', f"Fornecedor '{dados[0]}'")
        except Exception as e:
            flash(f"Erro ao adicionar fornecedor: {e}", "danger")
        return redirect(url_for('gerenciar_fornecedores'))

    fornecedores = db.executar("SELECT * FROM fornecedores WHERE usuario_id = %s ORDER BY nome", (user_id,),
                               fetch='all')
    return render_template('gerenciar_fornecedores.html', fornecedores=fornecedores)


@app.route('/fornecedor/excluir/<int:id>', methods=['POST'])
@login_required
def excluir_fornecedor(id):
    user_id = session['usuario_id']
    try:
        res = db.executar("DELETE FROM fornecedores WHERE id = %s AND usuario_id = %s", (id, user_id))
        if res > 0:
            flash("Fornecedor excluído.", "success")
            db.registrar_auditoria(user_id, 'Excluir Fornecedor', f"Fornecedor ID: {id}")
        else:
            flash("Fornecedor não encontrado ou você não tem permissão.", "danger")
    except Exception as e:
        flash(f"Erro ao excluir: {e}", "danger")
    return redirect(url_for('gerenciar_fornecedores'))


@app.route('/movimentacoes', methods=['GET', 'POST'])
@login_required
def movimentar_estoque():
    user_id = session['usuario_id']
    if request.method == 'POST':
        try:
            codigo_bebida = request.form['codigo_bebida']
            quantidade = int(request.form['quantidade'])
            tipo = request.form['tipo']
            observacao = request.form['observacao']
            fornecedor_id = request.form.get('fornecedor_id') or None

            bebida = db.executar("SELECT id, nome, quantidade FROM bebidas WHERE codigo = %s AND usuario_id = %s",
                                 (codigo_bebida, user_id), fetch='one')
            if not bebida:
                flash("Bebida com este código não encontrada no seu estoque.", "danger")
                return redirect(url_for('movimentar_estoque'))

            bebida_id = bebida['id']
            qtd_atual = bebida['quantidade']
            nova_qtd = qtd_atual

            if tipo == 'entrada':
                nova_qtd += quantidade
            elif tipo == 'saida':
                if quantidade > qtd_atual:
                    flash(f"Estoque insuficiente para a saída. Disponível: {qtd_atual}", "warning")
                    return redirect(url_for('movimentar_estoque'))
                nova_qtd -= quantidade

            db.executar("UPDATE bebidas SET quantidade = %s WHERE id = %s", (nova_qtd, bebida_id))
            db.executar(
                """INSERT INTO movimentacoes (bebida_id, tipo, quantidade, observacao, fornecedor_id, usuario_id)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (bebida_id, tipo, quantidade, observacao, fornecedor_id, user_id))

            db.registrar_auditoria(user_id, 'Movimentação de Estoque',
                                   f"Tipo: {tipo}, Bebida: '{bebida['nome']}', Qtd: {quantidade}")
            flash("Movimentação registrada com sucesso!", "success")
            return redirect(url_for('historico_movimentacoes'))

        except Exception as e:
            flash(f"Erro ao processar movimentação: {e}", "danger")
            return redirect(url_for('movimentar_estoque'))

    fornecedores = db.executar("SELECT id, nome FROM fornecedores WHERE usuario_id = %s ORDER BY nome", (user_id,),
                               fetch='all')
    return render_template('movimentacoes.html', fornecedores=fornecedores)


@app.route('/historico-movimentacoes')
@login_required
def historico_movimentacoes():
    user_id = session['usuario_id']
    movs = db.executar("""
                       SELECT m.data, b.nome as bebida_nome, m.tipo, m.quantidade, u.username, m.observacao
                       FROM movimentacoes m
                                JOIN bebidas b ON m.bebida_id = b.id
                                JOIN usuarios u ON m.usuario_id = u.id
                       WHERE m.usuario_id = %s
                       ORDER BY m.data DESC
                       """, (user_id,), fetch='all')
    return render_template('historico_movimentacoes.html', movimentacoes=movs)


@app.route('/relatorios', methods=['GET', 'POST'])
@login_required
def relatorios():
    user_id = session['usuario_id']
    if request.method == 'POST':
        data_inicio = request.form['data_inicio']
        data_fim = request.form['data_fim']

        query_mais_vendidos = """
                              SELECT p.nome, SUM(m.quantidade) as total_vendido
                              FROM movimentacoes m
                                       JOIN bebidas p ON m.bebida_id = p.id
                              WHERE m.tipo = 'saida'
                                AND m.usuario_id = %s
                                AND m.data BETWEEN %s AND %s
                              GROUP BY p.nome
                              ORDER BY total_vendido DESC
                              """
        mais_vendidos = db.executar(query_mais_vendidos, (user_id, data_inicio, data_fim), fetch='all')

        query_valor_total = """
                            SELECT SUM(m.quantidade * p.preco_venda) as total
                            FROM movimentacoes m
                                     JOIN bebidas p ON m.bebida_id = p.id
                            WHERE m.tipo = 'saida'
                              AND m.usuario_id = %s
                              AND m.data BETWEEN %s AND %s
                            """
        valor_total = db.executar(query_valor_total, (user_id, data_inicio, data_fim), fetch='one')

        return render_template('relatorios.html', mais_vendidos=mais_vendidos, valor_total=valor_total,
                               data_inicio=data_inicio, data_fim=data_fim)

    return render_template('relatorios.html', mais_vendidos=None, valor_total=None)


@app.route('/usuarios', methods=['GET', 'POST'])
@admin_required
def gerenciar_usuarios():
    if request.method == 'POST':
        action = request.form.get('action')
        id = request.form.get('id')
        username = request.form.get('username')
        nivel_acesso = request.form.get('nivel_acesso')
        password = request.form.get('password')

        try:
            if action == 'add':
                if not password:
                    flash("Senha é obrigatória para novos usuários.", "warning")
                else:
                    db.executar("INSERT INTO usuarios (username, password_hash, nivel_acesso) VALUES (%s, %s, %s)",
                                (username, hash_senha(password), nivel_acesso))
                    flash("Usuário adicionado.", "success")
            elif action == 'update':
                if password:
                    db.executar("UPDATE usuarios SET username=%s, password_hash=%s, nivel_acesso=%s WHERE id=%s",
                                (username, hash_senha(password), nivel_acesso, id))
                else:
                    db.executar("UPDATE usuarios SET username=%s, nivel_acesso=%s WHERE id=%s",
                                (username, nivel_acesso, id))
                flash("Usuário atualizado.", "success")
        except Exception as e:
            flash(f"Erro: {e}", "danger")
        return redirect(url_for('gerenciar_usuarios'))

    usuarios = db.executar("SELECT id, username, nivel_acesso FROM usuarios ORDER BY username", fetch='all')
    return render_template('gerenciar_usuarios.html', usuarios=usuarios)


@app.route('/usuario/excluir/<int:id>', methods=['POST'])
@admin_required
def excluir_usuario(id):
    if id == session['usuario_id']:
        flash("Você não pode excluir seu próprio usuário.", "warning")
        return redirect(url_for('gerenciar_usuarios'))
    try:
        db.executar("DELETE FROM usuarios WHERE id=%s", (id,))
        flash("Usuário excluído.", "success")
    except Exception as e:
        flash(f"Erro ao excluir: {e}", "danger")
    return redirect(url_for('gerenciar_usuarios'))


@app.route('/auditoria')
@admin_required
def historico_auditoria():
    auditorias = db.executar("""
                             SELECT a.data, u.username, a.acao, a.detalhes
                             FROM auditoria a
                                      LEFT JOIN usuarios u ON a.usuario_id = u.id
                             ORDER BY a.data DESC
                             """, fetch='all')
    return render_template('historico_auditoria.html', auditorias=auditorias)


if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5001)
