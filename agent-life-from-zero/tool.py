import ast, operator
from pathlib import Path

WORKDIR = Path("workspace").resolve()

_OPS = {ast.Add: operator.add, 
        ast.Sub: operator.sub, 
        ast.Mult: operator.mul,
        ast.Div: operator.truediv, 
        ast.Pow: operator.pow, 
        ast.Mod: operator.mod,
        ast.USub: operator.neg, 
        ast.UAdd: operator.pos}

def calculater(expression:str) -> str:
    def ev(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance()