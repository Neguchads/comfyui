# Segurança

## Política

Publique somente código, testes, documentação genérica e templates revisados. A lista positiva está em `.gitignore` e no validador `scripts/check_publication.py`.

Credenciais, `.env`, tokens, chaves, modelos, imagens, áudio, vídeos, entradas pessoais, embeddings, logs, históricos de sessões, ambientes, caches e backups ficam fora do repositório. O autor do commit usa endereço noreply do GitHub. Não acrescente serviços pagos ou secrets de CI para executar estes testes locais.

O validador local detecta caminhos pessoais, imagens embutidas e alguns padrões de credenciais, e verifica o índice Git. Ele complementa a proteção nativa do GitHub; nenhum detector cobre todos os dados sensíveis. Revise o diff antes de cada envio. Em caso de vazamento, revogue a credencial antes de corrigir o histórico.

A branch principal requer PR e bloqueia exclusão/force push conforme configuração verificada pelo mantenedor. Em um projeto com mantenedor único, revisão técnica local continua necessária; não confundir PR com aprovação independente. Não executar automaticamente código de PRs externos com credenciais ou acesso ao computador/GPU.

## Relato de vulnerabilidade

Use o canal privado em Security > Report a vulnerability quando disponível no GitHub. Não abra issue pública contendo segredos, dumps de memória ou dados pessoais. Se esse canal estiver indisponível, peça um contato privado ao mantenedor sem divulgar a vulnerabilidade publicamente.

## Escopo

As ferramentas consultam o servidor local e geram relatórios privados. O fluxo real depende de ComfyUI/custom nodes/modelos externos, com suas próprias políticas de segurança. Downloads, execução de nodes novos, exclusões e exposição de servidor exigem revisão e autorização próprias.
