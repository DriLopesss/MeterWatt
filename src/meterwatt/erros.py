"""Erros que viram resposta para quem usa o aplicativo.

A mensagem é escrita para a pessoa ler na tela. Nunca coloque aqui detalhes internos
(nomes de tabela, caminhos, dados de outra conta).
"""


class ErroDeNegocio(Exception):
    def __init__(self, mensagem, status=400, codigo=None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status
        self.codigo = codigo


class NaoEncontrado(ErroDeNegocio):
    """Usado também quando o recurso existe mas é de outra conta: a resposta é a mesma."""

    def __init__(self, mensagem="Não encontrado."):
        super().__init__(mensagem, 404, "nao_encontrado")
