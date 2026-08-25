# Spec — Fechar as pendências conhecidas antes de hospedar

**Data:** 2026-08-24 · **Origem:** pedido do dono do projeto, com as decisões da
sessão de planejamento anotadas ao final.

## Objetivo

Deixar a aplicação sólida o bastante para ser hospedada publicamente, fechando
cinco frentes de pendência conhecida. Ao final, a lista do que resta deve conter
**apenas** itens que dependem do ambiente real de hospedagem.

## Frente 1 — Classificação vestido/saia

Uma peça com aparência de vestido foi classificada como `saia` pelo FashionCLIP.
Investigar se foi ambiguidade da imagem ou confusão sistemática entre as classes
nos rótulos em inglês do zero-shot. Revisar os prompts de `vestido` e `saia`
tornando-os mais distintivos (cobertura do tronco, comprimento), rodar o script
de calibração contra as imagens de teste já salvas, e reportar se confiança e
acerto melhoraram. Adicionar segunda camada de defesa na composição de look e
documentar a limitação conhecida no código com clareza.

## Frente 2 — Abuso e custo

- Quota máxima de peças por usuário: valor generoso mas finito (ex.: 150),
  configurável por variável de ambiente.
- Rate limiting nas rotas de upload de peça e `/analyze`, reaproveitando o
  slowapi já usado em auth, com teto por hora que barre rajada sem incomodar uso
  normal.
- Hash perceptual da imagem no upload, para recusar reenvio da mesma imagem pelo
  mesmo usuário — proteção adicional contra esgotar a quota de forma barata.

## Frente 3 — Envio de e-mail real (pré-requisito das frentes 4)

Implementar envio de e-mail. É pré-requisito da verificação de e-mail e da
correção da enumeração de conta, portanto vem primeiro.

## Frente 4 — Verificação de e-mail e enumeração de conta (item #4)

- Ao cadastrar, o usuário recebe e-mail com link/código; a conta fica marcada
  como não verificada até confirmar.
- Decidir e documentar se isso bloqueia o login ou apenas sinaliza.
- Resposta de cadastro genérica quando o e-mail já existir, com notificação ao
  dono da conta existente em vez de exposição na resposta da API.

## Frente 5 — Prontidão para produção

- Storage do rate limiter do slowapi sai de memória e passa a Redis, para
  funcionar com múltiplos workers. Documentar a instalação no README como foi
  feito para o PostgreSQL.
- Avaliar e implementar a troca do JWT de `localStorage` para cookie `httpOnly`,
  ou documentar o que impede.
- Seção de checklist de deploy no README, cobrindo HTTPS e o que só se aplica
  fora do ambiente local.

## Entrega final

Suíte completa verde, validação manual de ponta a ponta (cadastro com
verificação, login, upload respeitando quota e rate limit, geração de look), e
relatório com contagem de testes antes/depois e a lista atualizada do que resta
exclusivamente para o momento de hospedagem.

---

## Decisões tomadas no planejamento

### D1 — Envio de e-mail: abstração com dois backends, Mailpit agora
Interface `EmailSender` com backends selecionados por `EMAIL_BACKEND`:
`smtp` (Mailpit local, hoje) e `resend` (produção). Mailpit sobe via Docker
Compose — Docker já está instalado e funcionando na máquina (29.7.2, Compose
v5.5.0). Custo zero, nenhuma conta externa, nenhum endereço real exposto, e a
troca para produção é de variável de ambiente, não de código.

Descartado: só Mailpit (exigiria mexer no código ao hospedar); só Resend (exige
conta hoje e, sem domínio verificado, só envia para o próprio endereço do dono —
o que impede testar cadastro de outros usuários).

### D2 — Cookie httpOnly: implementar agora, backend e frontend
Localmente funciona sem HTTPS porque `localhost:3000` e `localhost:8000` são o
mesmo host para efeito de cookie. Efeito colateral positivo: `AuthedImage`
deixa de ser necessário, porque `<img src>` volta a funcionar com cookie
automático. Em produção exigirá `SameSite=None; Secure`, isto é, HTTPS — que já
entra no checklist de deploy.

⚠️ Consequência a tratar no plano: cookie automático reintroduz superfície de
CSRF que o header `Authorization` não tinha. Mitigação obrigatória:
`SameSite=Lax` e CORS restrito às origens conhecidas.

### D3 — Estrutura: um plano só, executado em ordem
Uma branch, revisão entre tarefas, um relatório final. E-mail primeiro, por ser
pré-requisito.

### D4 — Redis: via Docker Compose, junto do Mailpit
Escolha do executor, autorizada pelo pedido ("sua escolha"). Mantém uma única
stack local para as duas dependências novas. Redis não está instalado na máquina.

### D5 — Bloqueio de login por e-mail não verificado: NÃO bloqueia por padrão
O pedido delegou a decisão. Escolha: `REQUIRE_VERIFIED_EMAIL` com padrão `false`
— a conta é marcada e o estado é exposto, mas o login continua funcionando.

Motivo: bloquear o login torna a aplicação inutilizável se o Mailpit não estiver
de pé, e os dois usuários já existentes no banco não têm como ter verificado
nada. Uma trava que depende de um contêiner rodando é uma trava que vai prender
o próprio dono do projeto. A flag existe para ser ligada quando houver entrega
de e-mail confiável.
