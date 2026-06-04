# HARIA — Completamento CRUD (piano)

Obiettivo: ogni dominio gestito da HARIA deve avere CRUD completa (Create/Read/Update/Delete) esposta come tool, così l'assistente può correggere/cancellare da solo senza intervento manuale (come è successo coi duplicati calendar).

Legenda: C=create R=read U=update D=delete. ✅ presente · ➕ da aggiungere.

## agenda

| Dominio | C | R | U | D | Da aggiungere |
|---|---|---|---|---|---|
| promemoria | set_reminder | list_reminders | ➕ `update_reminder` | cancel_reminder | edit testo/orario/cron + reschedule |
| task/todo | add_task | get_tasks | complete_task (solo stato) ➕ `update_task` | remove_task | rename/scadenza/desc/owner/stato via `todo.update_item` |
| eventi | add_event | get_events | update_event ✅ | delete_event ✅ | — (già fatto v0.1.48) |

## food_diary

| Dominio | C | R | U | D | Da aggiungere |
|---|---|---|---|---|---|
| pasti | log_meal | get_meals | update_meal | delete_meal | — completo |
| piano | set_plan_meal/plan_week | get_meal_plan | set_plan_meal | delete_plan_meal | — completo |
| profili | set_diet_profile | get_diet_profile | set_diet_profile | ➕ `delete_profile` | cancella profilo membro |
| peso | log_weight | get_weight_history | ➕ `update_weight` | ➕ `delete_weight` | id in history; correggi/cancella per id |
| idratazione | log_hydration | get_hydration | — | ➕ `delete_hydration` | annulla ultimo log |
| spesa | add_shopping_items | get_shopping_list | set_shopping_price | ➕ `remove_shopping_item` (oltre clear) | rimuovi singola voce per nome |
| dispensa | add_pantry_items | get_pantry | ➕ `update_pantry_item` | consume/clear | edit qty/categoria/scadenza |
| diete rif. | save_diet | list_diets | save_diet (overwrite) | ➕ `delete_diet` | cancella file dieta per nome |

## bollette
- Solo `update_bill` (set/overwrite idempotente con dedup). Dominio specifico, CRUD non applicabile. Nessuna azione.

## Backend nuovo (memory.py)
- `update_reminder(id, user_id, message?, remind_at?, recurring?) -> dict|None`
- `remove_shopping_item(name) -> int`
- `delete_profile(member) -> bool`
- `get_weight_history` → includere `id`; `update_weight(id, weight?, bmi?) -> bool`; `delete_weight(id) -> bool`
- `delete_last_hydration(member) -> bool`
- `update_pantry_item(name, qty?, category?, expires_on?) -> int`

## Tool nuovi
- agenda: `update_reminder`, `update_task`
- food_diary: `delete_profile`, `update_weight`, `delete_weight`, `delete_hydration`, `remove_shopping_item`, `update_pantry_item`, `delete_diet`

## Dopo
- Compile check, bump versione, deploy.
- Code review completa del progetto (sicurezza, consistenza, bug).
