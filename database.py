import psycopg2
import psycopg2.extras
import hashlib
import os


class Database:
    """
    Gerencia a conexão e as operações com o banco de dados PostgreSQL.
    """

    def __init__(self):
        db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:SENHA_DO_POSTGRE_AQUI@localhost/estoque_bebidas')
        try:
            self.conn = psycopg2.connect(db_url, client_encoding='UTF8')
            print("Conexão com o PostgreSQL bem-sucedida!")
        except psycopg2.OperationalError as e:
            print(f"ERRO CRÍTICO: Não foi possível conectar ao PostgreSQL: {e}")
            raise

        self._criar_tabelas()

    def _criar_tabelas(self):
        """
        Garante que todas as tabelas necessárias existam no banco de dados.
        """
        scripts = [
            '''CREATE TABLE IF NOT EXISTS usuarios
            (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                nivel_acesso TEXT CHECK (
                    nivel_acesso IN ('administrador', 'operador')
                ) NOT NULL DEFAULT 'operador'
            );''',
            '''CREATE TABLE IF NOT EXISTS categorias
            (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
                UNIQUE (nome, usuario_id)
            );''',
            '''CREATE TABLE IF NOT EXISTS fornecedores
            (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                contato TEXT,
                endereco TEXT,
                cnpj TEXT,
                email TEXT,
                usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
                UNIQUE (nome, usuario_id)
            );''',
            '''CREATE TABLE IF NOT EXISTS bebidas
            (
                id SERIAL PRIMARY KEY,
                codigo TEXT,
                nome TEXT NOT NULL,
                categoria_id INTEGER REFERENCES categorias(id) ON DELETE SET NULL,
                preco_custo REAL,
                preco_venda REAL,
                quantidade INTEGER DEFAULT 0,
                quantidade_minima INTEGER DEFAULT 0,
                imagem_url TEXT, -- NOVO CAMPO PARA A IMAGEM
                usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
                UNIQUE (codigo, usuario_id)
            );''',
            '''CREATE TABLE IF NOT EXISTS movimentacoes
            (
                id SERIAL PRIMARY KEY,
                bebida_id INTEGER REFERENCES bebidas(id) ON DELETE CASCADE,
                tipo TEXT CHECK (
                                    tipo IN(
                                    'entrada',
                                    'saida',
                                    'devolucao_cliente',
                                    'devolucao_fornecedor',
                                    'ajuste'
                                           )
                ),
                quantidade INTEGER,
                data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                observacao TEXT,
                fornecedor_id INTEGER REFERENCES fornecedores(id) ON DELETE SET NULL,
                usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL
            );''',
            '''CREATE TABLE IF NOT EXISTS auditoria
            (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
                acao TEXT NOT NULL,
                data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                detalhes TEXT
            );'''
        ]
        with self.conn.cursor() as cursor:
            for script in scripts:
                cursor.execute(script)
        self.conn.commit()
        print("Tabelas (com campo de imagem) verificadas/criadas com sucesso.")

    def executar(self, query, params=(), fetch=None):
        try:
            with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                cursor.execute(query, params)
                self.conn.commit()
                if fetch == 'one':
                    return cursor.fetchone()
                if fetch == 'all':
                    return cursor.fetchall()
                return cursor.rowcount
        except psycopg2.Error as e:
            self.conn.rollback()
            print(f"Erro no banco de dados: {e}")
            raise Exception(f"Erro no banco de dados: {str(e)}")

    def registrar_auditoria(self, usuario_id, acao, detalhes=None):
        try:
            self.executar(
                "INSERT INTO auditoria (usuario_id, acao, detalhes) VALUES (%s, %s, %s)",
                (usuario_id, acao, detalhes)
            )
        except Exception as e:
            print(f"Erro ao registrar auditoria: {e}")

    def close(self):
        self.conn.close()


def hash_senha(senha):
    return hashlib.sha256(senha.encode()).hexdigest()
