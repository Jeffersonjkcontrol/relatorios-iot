# 📊 Relatórios IoT — Sua fábrica na palma da mão

**Sistema de monitoramento e análise de produção para máquinas injetoras**

---

## O problema que resolvemos

Hoje, para saber o que aconteceu na produção, alguém precisa:

- ❌ Anotar paradas em papel ou planilha (quando anota)
- ❌ Perguntar ao operador "por que a máquina ficou parada ontem?"
- ❌ Esperar o fechamento do mês para descobrir que a produtividade caiu
- ❌ Montar relatórios manualmente, juntando dados de várias fontes
- ❌ Confiar na memória para saber qual molde rodou em qual máquina

**Resultado:** decisões atrasadas, paradas repetidas pelos mesmos motivos, e horas de trabalho administrativo que não agregam valor.

## A solução

Um sistema que **já está conectado às suas injetoras** e transforma os dados que elas geram em respostas prontas — acessível de qualquer computador, celular ou tablet da fábrica, pelo navegador, sem instalar nada:

```
🌐  http://relatorios.local
```

Cada pessoa tem seu login. Operadores consultam; gestores administram.

---

## O que o sistema faz por você

### 📡 1. Dashboard Ao Vivo — a fábrica em tempo real

Uma tela que mostra **todas as máquinas ao mesmo tempo**, atualizando sozinha a cada 10 segundos:

- 🟢 **Verde** — máquina produzindo agora
- 🟡 **Amarelo** — máquina ociosa (sem ciclo há alguns minutos)
- 🔴 **Vermelho** — máquina parada

Cada cartão mostra o ciclo atual, a média recente, a tendência (subindo/descendo) e **qual molde está montado**. Ideal para deixar aberto numa TV no chão de fábrica ou na sala do gestor.

**Benefício direto:** você enxerga uma máquina parada em segundos — não horas depois.

### ⏸️ 2. Análise de Paradas — saiba POR QUE e POR QUANTO TEMPO

O sistema detecta **automaticamente** cada parada de cada máquina e monta o quadro completo:

- Quantas paradas ocorreram, quanto tempo somaram e a duração média
- **Ranking dos motivos** (Pareto): qual problema mais rouba tempo da fábrica
- Qual máquina mais para e qual foi a maior parada
- **Quem estava na máquina** e o tempo médio de retorno por funcionário

Como funciona no dia a dia: quando a máquina para, o operador **bate o cartão do motivo** no leitor RFID (Falta de material, Troca de molde, Manutenção...). Só isso. O sistema faz todo o resto — cruza horários, calcula durações e monta os relatórios.

**Benefício direto:** em vez de discutir "impressões", a reunião de produção começa com fatos: *"Troca de molde consumiu 14 horas essa semana — 40% de todo o tempo parado."*

### 📈 3. OEE — o indicador que as melhores fábricas usam

OEE (Overall Equipment Effectiveness) é o padrão mundial para medir eficiência de máquina. O sistema calcula com um clique:

- **Disponibilidade** — quanto do tempo planejado a máquina realmente rodou
- **Performance** — o quão perto do ciclo ideal ela produziu
- **Qualidade** — proporção de peças boas vs. refugo

Com cores intuitivas (verde/amarelo/vermelho), memorial de cálculo transparente e filtro inteligente que separa paradas de ciclos reais.

**Benefício direto:** você descobre ONDE está perdendo dinheiro — se é máquina parada, ritmo lento ou refugo — e ataca a causa certa.

### 📋 4. Relatórios em PDF e Excel — sem trabalho manual

Qualquer histórico de qualquer máquina, em qualquer período:

1. Escolhe a máquina e a variável
2. Escolhe o período
3. Clica em **Visualizar** — vê na tela antes de exportar
4. Baixa em **PDF** (pronto para imprimir/enviar) ou **CSV** (abre no Excel)

Os relatórios saem formatados, com estatísticas calculadas e o contexto de cada leitura (molde em uso, etc.).

**Benefício direto:** o relatório que levava uma tarde para montar sai em 30 segundos.

### 🔧 5. Análise de Moldes — cada molde sob controle

O sistema agrupa a produção **por molde** automaticamente:

- Quantas peças cada molde produziu no período
- Ciclo médio real vs. ciclo especificado do molde
- **Desvio %** com alerta de cor — molde rodando 15% mais lento que o especificado fica vermelho
- Em quais máquinas cada molde rodou

**Benefício direto:** identifica molde desgastado ou mal regulado antes que vire refugo e atraso de entrega.

### 🤖 6. Assistente de IA — pergunte em português, receba análise

A funcionalidade que impressiona: um chat onde você **pergunta em linguagem natural** e a IA consulta os dados reais e responde na hora:

> *"Quais máquinas mais pararam essa semana?"*
>
> *"Qual o ciclo médio da injetora 82 hoje?"*
>
> *"Calcule o OEE da inj80 ontem, ciclo ideal 60 segundos"*
>
> *"Me gera um PDF das paradas dos últimos 7 dias"*

A IA responde com tabelas, destaca problemas que você nem perguntou (ex.: *"a injetora 03 está parada há 24 horas contínuas — verifique"*) e sugere ações. Ela conhece as regras da SUA fábrica: turnos, metas, ciclos ideais de cada molde — tudo configurável.

**Benefício direto:** qualquer pessoa da equipe extrai análises que antes dependiam de alguém "que sabe mexer no Excel".

---

## Como cada pessoa usa

### 👷 Operador (2 minutos de treinamento)

1. No início do turno: **bate o cartão de identificação** no leitor da máquina
2. Se a máquina parar: **bate o cartão do motivo** (os cartões ficam pendurados ao lado do leitor)
3. Pronto. É só isso que o sistema precisa de você.

Se quiser, pode acessar o sistema pelo celular e acompanhar sua máquina — mas não é obrigatório.

### 👨‍💼 Líder / Supervisor

1. Abre `http://relatorios.local` no navegador (salva nos favoritos)
2. **Ao Vivo** para acompanhar o turno em tempo real
3. **Paradas** no fim do turno: confere os motivos e cobra apontamentos faltantes
4. Baixa o PDF do turno se precisar reportar

### 👔 Gestor / Diretor

1. **OEE** semanal por máquina — enxerga a eficiência real
2. **Paradas** → Pareto de motivos — prioriza os investimentos (o que dá mais retorno atacar?)
3. **Moldes** — decide manutenção de ferramental com base em dados
4. **IA** — perguntas rápidas sem depender de ninguém montar relatório
5. Administra usuários, cadastros de motivos e configurações

---

## Perguntas frequentes

**"Vai dar trabalho para o operador?"**
Não. O único gesto novo é bater um cartão quando a máquina para — leva 2 segundos. Todo o resto é automático.

**"E se ninguém bater o cartão?"**
O sistema mostra o **% de paradas com motivo apontado**. Se estiver baixo, o gestor sabe exatamente onde cobrar. A parada é registrada de qualquer forma (com ou sem motivo) — nada se perde.

**"Precisa de internet?"**
O sistema roda num mini computador **dentro da fábrica**. O acesso pela rede local funciona mesmo se a internet cair. Internet é necessária apenas para buscar os dados das máquinas na plataforma e para o assistente de IA.

**"Os dados ficam seguros?"**
Sim. Cada pessoa tem login e senha; operadores não conseguem alterar configurações; e o sistema faz **backup automático todos os dias**. O suporte técnico acessa por canal criptografado apenas para manutenção.

**"Funciona no celular?"**
Sim — qualquer dispositivo com navegador conectado na rede da fábrica: PC, notebook, tablet, celular.

**"E se aparecer um problema ou precisarmos de um relatório novo?"**
O sistema tem suporte ativo e evolui continuamente — melhorias chegam por atualização remota, sem parar a fábrica e sem ninguém precisar ir até as máquinas.

---

## Resumo: o que muda com o sistema

| Antes | Depois |
|---|---|
| Paradas anotadas em papel (às vezes) | Todas as paradas registradas automaticamente, com motivo e duração |
| "Acho que a máquina 23 para muito" | "A inj23 parou 16 vezes em 24h — 21h53m paradas. Motivo nº 1: troca de molde" |
| Relatório manual, uma tarde de trabalho | PDF pronto em 30 segundos |
| Eficiência descoberta no fechamento do mês | OEE de qualquer período, em qualquer momento |
| Molde com problema descoberto pelo refugo | Desvio de ciclo alertado em amarelo/vermelho na tela |
| Análise depende de quem domina Excel | Qualquer um pergunta à IA em português |

---

## Comece hoje

1. Acesse **http://relatorios.local** de qualquer dispositivo da rede da fábrica
2. Entre com o login fornecido pelo gestor
3. Explore o **Ao Vivo** primeiro — é a porta de entrada natural
4. Na primeira semana, foque em **apontar os motivos de parada** — é o combustível das análises

> *"O que não se mede, não se gerencia."* — o sistema mede por você. Gerenciar fica com quem entende do negócio: **vocês**.

---

**Suporte técnico:**
Jefferson Piccirillo — JKControl
✉️ jefferson.piccirillo@jkcontrol.com.br
