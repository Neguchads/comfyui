# Repositório público ComfyUI

Use README.md para instalação e SECURITY.md para publicação. Preserve os arquivos da instalação do usuário; trabalhe em cópias dos templates.

Teste com `python -m unittest discover -s tests -p 'test_*.py'`. Antes de commit/envio, execute `python scripts/check_publication.py --staged` e revise o diff.

Adições mudam a lista positiva de publicação e exigem revisão. Relatórios/inputs/modelos/credenciais permanecem locais. Main recebe mudanças por PR; não usar force push ou contornar proteção de segredos. Confirme autorização para novas publicações e operações destrutivas. Revisão independente é adequada para mudanças de execução, segurança ou sanitização de workflows; cada editor recebe escopo exclusivo.
