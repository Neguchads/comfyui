# ComfyUI — workflows e ferramentas locais

Coleção independente de ferramentas Python e um template PoseClone para uma instalação ComfyUI existente. ComfyUI é um projeto de Comfy-Org: https://github.com/Comfy-Org/ComfyUI.

## Conteúdo

- `src/pc_tools.py`: inventário, diff, rastreamento, validação estrutural, resumo de logs, cache e métricas compactas.
- `src/audit_delegation.py`: auditoria estruturada de sessões Codex locais; relatórios são locais e privados.
- `workflows/poseclone-qwen21.json`: template Qwen Image 2.1 + LoRA PoseStudio V1.1 + VNCCS. Pose alimenta image_1; sua referência alimenta image_2.
- Testes determinísticos e conferência da publicação, sem GPU ou API paga.

Modelos, nodes externos e Python Portable são obtidos pelos canais dos respectivos autores. O template referencia Qwen2.1 int8 convrot, Qwen3VL int8 convrot, VAE Qwen bf16 e VNCCS_QI2_PoseStudioV1.1. Verifique versões e nomes no seu catálogo instalado; importar JSON não instala dependências nem comprova fidelidade.

O arquivo publicado usa `reference.png` como placeholder. Carregue uma imagem própria na interface, escolha/aplique a pose no editor e revise a ordem das referências. A seed de exemplo é 42. Não são distribuídas imagens ou capturas. O template sanitizado passou no preflight; não foi reexecutado em GPU como distribuição pública. Pose, rosto, roupa, anatomia e enquadramento exigem avaliação separada.

O prompt mantém a instrução usada nos testes operacionais: a pose vem de image1 e o personagem de image2. As saídas usam `output/Imagens/Qwen_PoseClone*.png`. Referência, seed e identificadores genéricos da distribuição permanecem diferentes das entradas locais; não foi realizado um novo teste de geração deste template público.

Esta coleção contém apenas o template PoseClone. Outros workflows, a biblioteca de poses, o lançador Windows e ajustes locais no carregador do ComfyUI não são distribuídos aqui. Esses componentes exigem revisão própria antes de uma eventual publicação.

## Uso em Windows / PowerShell

Python 3.12+, somente biblioteca padrão. Na raiz do clone:

```powershell
$env:POSECLONE_COMFY_DIR = 'D:\AI\ComfyUI\ComfyUI'
python src/pc_tools.py status
python src/pc_tools.py validate workflows/poseclone-qwen21.json --live
python -m unittest discover -s tests -p 'test_*.py'
python scripts/check_publication.py
```

A variável aponta para a pasta que contém main.py, models e custom_nodes. Sem ela, a ferramenta procura uma instalação irmã ComfyUI/ComfyUI. HTTP é limitado a 127.0.0.1:8188, sem proxies ou redirecionamentos. `run` exige opt-in, formato API, fila vazia e classes locais previamente revisadas; o template VNCCS/Qwen utiliza nodes fora dessa lista e deve ser executado pela interface após revisão. Custom nodes podem acessar rede por conta própria; revise sua origem.

Saídas têm JSON curto e apontam para relatórios locais. `src/cache` e `src/reports`, ambientes, imagens e pesos são ignorados. Bytes medem volume de texto; contadores nativos de tokens não comprovam economia ou faturamento. Não publique relatórios de sessões ou inputs pessoais.

## Contribuição

Use branch e pull request. Antes de enviar, execute testes e `python scripts/check_publication.py --staged`. A lista positiva de arquivos deve ser revisada quando o escopo mudar. `.gitignore` pode ser contornado com `git add -f`; a conferência do índice também rejeita arquivos fora da lista. Actions ficam desativadas nesta versão inicial; testes são locais. Veja SECURITY.md e NOTICE.md.
