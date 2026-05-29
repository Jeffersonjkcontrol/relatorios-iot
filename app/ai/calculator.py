"""Calculadora segura para a IA.

Usa AST do Python pra validar a expressão ANTES de executar — só permite
operadores aritméticos e funções matemáticas seguras. Bloqueia qualquer
tentativa de import, eval, acesso a atributos, etc.
"""
from __future__ import annotations

import ast
import math
import operator
from statistics import mean, median, stdev, pstdev, variance, pvariance

# Operadores permitidos
_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.FloorDiv: operator.floordiv,
}

# Funções e constantes permitidas
_ALLOWED_NAMES = {
    # constantes
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    # math básico
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "floor": math.floor,
    "ceil": math.ceil,
    "trunc": math.trunc,
    "factorial": math.factorial,
    # builtins seguros
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "pow": pow,
    "len": len,
    "int": int,
    "float": float,
    # estatística
    "mean": mean,
    "median": median,
    "stdev": stdev,
    "pstdev": pstdev,
    "variance": variance,
    "pvariance": pvariance,
}

MAX_EXPR_LEN = 500


class CalcError(Exception):
    pass


def _eval(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise CalcError(f"Constante não permitida: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPS:
            raise CalcError(f"Operador não permitido: {op_type.__name__}")
        return _ALLOWED_OPS[op_type](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPS:
            raise CalcError(f"Operador não permitido: {op_type.__name__}")
        return _ALLOWED_OPS[op_type](_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id not in _ALLOWED_NAMES:
            raise CalcError(f"Nome não permitido: {node.id}")
        return _ALLOWED_NAMES[node.id]
    if isinstance(node, ast.Call):
        func = _eval(node.func)
        if not callable(func):
            raise CalcError("Tentativa de chamar algo não-callable")
        args = [_eval(a) for a in node.args]
        return func(*args)
    if isinstance(node, ast.List):
        return [_eval(e) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(e) for e in node.elts)
    raise CalcError(f"Operação não permitida: {type(node).__name__}")


def safe_calc(expression: str) -> float | int | list:
    """Avalia uma expressão matemática de forma segura.

    Aceita: + - * / % ** //, comparações implícitas, listas, e funções
    matemáticas/estatísticas (sqrt, log, sin, mean, median, stdev, etc.).

    Bloqueia: import, atribuição, acesso a atributos, lambdas, eval/exec,
    funções não listadas em _ALLOWED_NAMES.
    """
    if not isinstance(expression, str):
        raise CalcError("Expressão deve ser string")
    expression = expression.strip()
    if not expression:
        raise CalcError("Expressão vazia")
    if len(expression) > MAX_EXPR_LEN:
        raise CalcError(f"Expressão muito longa (máx {MAX_EXPR_LEN} chars)")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"Erro de sintaxe: {e}")
    return _eval(tree.body)
