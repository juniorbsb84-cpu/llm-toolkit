"""Checagem minima: python test_esquadrao.py (sem framework)."""
import esquadrao as esq

P = ["claude-sonnet-5-5@xhigh", "claude-opus-5-5@high", "qwen3.8-max", "claude-opus-5-5"]
assert esq._posicao(P, "claude-sonnet-5-5", "xhigh") == 0
assert esq._posicao(P, "claude-sonnet-5-5", "medium") == len(P)      # effort abaixo do exigido
assert esq._posicao(P, "claude-opus-5-5", "max") == 1                # max >= high
assert esq._posicao(P, "claude-opus-5-5", "low") == 3                # cai no nivel sem @
assert esq._posicao(P, "alibaba-token-plan/qwen3.8-max", "high") == 2
assert esq._posicao(P, "modelo-desconhecido", "high") == len(P)

esq.AJUSTES.clear()
esq.AJUSTES.update({"a": ("claude-opus-5-5", "low"), "b": ("claude-sonnet-5-5", "xhigh"),
                    "c": ("qwen3.8-max", "high")})
assert esq.rota_final()["codigo"][0] == "b"
print("ok")
