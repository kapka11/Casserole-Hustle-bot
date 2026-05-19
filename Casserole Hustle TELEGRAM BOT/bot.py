import asyncio, random, time, re, os, psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TOKEN = "8206764435:AAFb5vDu87bAx7gR1iBuq0n5wXLIzug2ikY"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
COOLDOWN = 600
COMMISSION = 0.2
C_PRICE = 5
S_PRICE = 20
PROXY = os.environ.get("TG_PROXY", "")

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER, chat_id INTEGER,
            username TEXT DEFAULT '', first_name TEXT DEFAULT '',
            total_casseroles INTEGER DEFAULT 0, casseroles INTEGER DEFAULT 0,
            total_syrniki INTEGER DEFAULT 0, syrniki INTEGER DEFAULT 0,
            casserole_actions INTEGER DEFAULT 0, level INTEGER DEFAULT 1,
            balance INTEGER DEFAULT 0,
            last_casserole REAL DEFAULT 0, last_salary REAL DEFAULT 0,
            next_level_at INTEGER DEFAULT 10,
            PRIMARY KEY (user_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS gsyrniki (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '', first_name TEXT DEFAULT '',
            total_syrniki INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()

def get_user(uid, cid, uname="", fname=""):
    conn = get_db()
    cur = conn.execute("SELECT * FROM users WHERE user_id=? AND chat_id=?", (uid, cid))
    row = cur.fetchone()
    if row:
        if uname or fname:
            conn.execute("UPDATE users SET username=?, first_name=? WHERE user_id=? AND chat_id=?", (uname, fname, uid, cid))
            conn.commit()
        conn.close()
        return dict(row)
    req = random.randint(5, 10)
    conn.execute("INSERT INTO users (user_id,chat_id,username,first_name,next_level_at) VALUES (?,?,?,?,?)", (uid, cid, uname, fname, req))
    conn.commit()
    conn.close()
    return {"user_id":uid,"chat_id":cid,"username":uname,"first_name":fname,"total_casseroles":0,"casseroles":0,"total_syrniki":0,"syrniki":0,"casserole_actions":0,"level":1,"balance":0,"last_casserole":0,"last_salary":0,"next_level_at":req}

def upd_user(uid, cid, **kw):
    conn = get_db()
    conn.execute(f"UPDATE users SET {', '.join(f'{k}=?' for k in kw)} WHERE user_id=? AND chat_id=?", (*kw.values(), uid, cid))
    conn.commit()
    conn.close()

def do_casserole(uid, cid, uname, fname):
    u = get_user(uid, cid, uname, fname)
    amount = random.randint(1, 20)
    syr = 0
    if random.random() < 0.20:
        lv = u["level"]
        if lv == 1: mx = 5
        elif lv == 2: mx = max(1, round(5 * lv / 1.5))
        else: mx = max(1, round(5 * lv / 2))
        syr = random.randint(1, mx)
    acts = u["casserole_actions"] + 1
    new_total = u["total_casseroles"] + amount
    new_cass = u["casseroles"] + amount
    new_tsyr = u["total_syrniki"] + syr
    new_syr = u["syrniki"] + syr
    lv = u["level"]
    nla = u["next_level_at"]
    lvup = False
    if acts >= nla:
        lv += 1
        lvup = True
        nla = acts + random.randint(5 + lv * 2, 10 + lv * 5)
    upd_user(uid, cid, total_casseroles=new_total, casseroles=new_cass, total_syrniki=new_tsyr, syrniki=new_syr, casserole_actions=acts, level=lv, next_level_at=nla, last_casserole=time.time())
    if syr > 0:
        conn = get_db()
        cur = conn.execute("SELECT total_syrniki FROM gsyrniki WHERE user_id=?", (uid,))
        r = cur.fetchone()
        if r:
            conn.execute("UPDATE gsyrniki SET total_syrniki=?, username=?, first_name=? WHERE user_id=?", (r[0]+syr, uname, fname, uid))
        else:
            conn.execute("INSERT INTO gsyrniki (user_id,username,first_name,total_syrniki) VALUES (?,?,?,?)", (uid, uname, fname, syr))
        conn.commit()
        conn.close()
    return {"amount":amount,"syr":syr,"lvup":lvup,"lv":lv,"rem":nla-acts,"total":new_total}

async def cmd_casserole(upd, ctx):
    u, c = upd.effective_user, upd.effective_chat
    uid, cid = u.id, c.id
    du = get_user(uid, cid, u.username or "", u.first_name or "")
    if time.time() - du["last_casserole"] < COOLDOWN:
        r = int(COOLDOWN - (time.time() - du["last_casserole"]))
        await upd.message.reply_text(f"⏳ Подождите ещё {r//60} мин {r%60} сек")
        return
    r = do_casserole(uid, cid, u.username or "", u.first_name or "")
    msg = f"🍳 Замутили {r['amount']} запеканок!"
    if r["syr"]: msg += f"\n🧀 Выпало {r['syr']} сырников!"
    if r["lvup"]: msg += f"\n🎉 Уровень {r['lv']}!"
    msg += f"\n📊 Всего: {r['total']}\n🎯 До уровня: {r['rem']} замуток"
    await upd.message.reply_text(msg)

async def cmd_me(upd, ctx):
    u, c = upd.effective_user, upd.effective_chat
    du = get_user(u.id, c.id, u.username or "", u.first_name or "")
    conn = get_db()
    cur = conn.execute("SELECT COUNT(*)+1 FROM users WHERE chat_id=? AND total_casseroles>(SELECT total_casseroles FROM users WHERE user_id=? AND chat_id=?)", (c.id, u.id, c.id))
    rank = cur.fetchone()[0]
    conn.close()
    rem = du["next_level_at"] - du["casserole_actions"]
    await upd.message.reply_text(
        f"👤 {u.first_name}{' (@'+u.username+')' if u.username else ''}\n"
        f"🍳 Запеканок: {du['total_casseroles']} (в наличии: {du['casseroles']})\n"
        f"🔢 Замутов: {du['casserole_actions']}\n"
        f"🧀 Сырников: {du['total_syrniki']} (в наличии: {du['syrniki']})\n"
        f"🪙 Запекоинов: {du['balance']}\n"
        f"📊 Уровень: {du['level']}\n"
        f"🎯 Осталось замуток: {rem}\n"
        f"🏆 Место: {rank}"
    )

async def cmd_top(upd, ctx):
    conn = get_db()
    c1 = conn.execute("SELECT * FROM users WHERE chat_id=? ORDER BY total_casseroles DESC LIMIT 10", (upd.effective_chat.id,)).fetchall()
    c2 = conn.execute("SELECT * FROM users WHERE chat_id=? ORDER BY level DESC LIMIT 10", (upd.effective_chat.id,)).fetchall()
    conn.close()
    msg = "🏆 <b>ТОП ЗАПЕКАНОЧНЫХ ЦЕНТРОВ</b>\n\n<b>По запеканкам:</b>\n"
    for i, r in enumerate(c1, 1):
        msg += f"{i}. {r['first_name'] or r['username'] or '#'+str(r['user_id'])} — {r['total_casseroles']} 🍳\n"
    msg += "\n<b>По уровню:</b>\n"
    for i, r in enumerate(c2, 1):
        msg += f"{i}. {r['first_name'] or r['username'] or '#'+str(r['user_id'])} — {r['level']} 🆙\n"
    await upd.message.reply_text(msg, parse_mode="HTML")

async def cmd_top_syr(upd, ctx):
    conn = get_db()
    rows = conn.execute("SELECT * FROM gsyrniki WHERE total_syrniki>0 ORDER BY total_syrniki DESC LIMIT 10").fetchall()
    conn.close()
    msg = "🧀 <b>ТОП ВЕЗУНЧИКОВ (ВСЕ ЧАТЫ)</b>\n\n"
    for i, r in enumerate(rows, 1):
        msg += f"{i}. {r['first_name'] or r['username'] or '#'+str(r['user_id'])} — {r['total_syrniki']} 🧀\n"
    await upd.message.reply_text(msg or "Пока никого нет", parse_mode="HTML")

async def cmd_gift(upd, ctx):
    t = get_target(upd)
    if not t:
        return await upd.message.reply_text("Ответьте на сообщение или укажите @пользователя!")
    if t.id == upd.effective_user.id:
        return await upd.message.reply_text("Себе нельзя!")
    args = ctx.args or re.findall(r'\d+', upd.message.text.split("подарить")[-1] if "подарить" in upd.message.text else "")
    if not args: return await upd.message.reply_text("Укажите количество!")
    try: amt = int(args[0])
    except: return await upd.message.reply_text("Число!")
    if amt <= 0: return await upd.message.reply_text("Положительное число!")
    conn = get_db()
    cur = conn.execute("SELECT casseroles FROM users WHERE user_id=? AND chat_id=?", (upd.effective_user.id, upd.effective_chat.id))
    r = cur.fetchone()
    if not r or r[0] < amt:
        conn.close()
        return await upd.message.reply_text("❌ Недостаточно запеканок!")
    conn.execute("UPDATE users SET casseroles=casseroles-? WHERE user_id=? AND chat_id=?", (amt, upd.effective_user.id, upd.effective_chat.id))
    conn.execute("UPDATE users SET casseroles=casseroles+? WHERE user_id=? AND chat_id=?", (amt, t.id, upd.effective_chat.id))
    conn.commit()
    conn.close()
    await upd.message.reply_text(f"✅ Подарено {amt} запеканок {t.first_name}!")

async def cmd_salary(upd, ctx):
    u, c = upd.effective_user, upd.effective_chat
    du = get_user(u.id, c.id, u.username or "", u.first_name or "")
    if time.time() - du["last_salary"] < COOLDOWN:
        r = int(COOLDOWN - (time.time() - du["last_salary"]))
        return await upd.message.reply_text(f"⏳ Через {r//60} мин {r%60} сек")
    upd_user(u.id, c.id, balance=du["balance"]+200, last_salary=time.time())
    await upd.message.reply_text("💰 Получено 200 запекоинов!")

async def cmd_givecoins(upd, ctx):
    t = get_target(upd)
    if not t:
        return await upd.message.reply_text("Ответьте на сообщение или укажите @пользователя!")
    if t.id == upd.effective_user.id:
        return await upd.message.reply_text("Себе нельзя!")
    if not ctx.args: return await upd.message.reply_text("Укажите сумму! /givecoins 100")
    try: amt = int(ctx.args[0])
    except: return await upd.message.reply_text("Число!")
    if amt <= 0: return await upd.message.reply_text("Положительное число!")
    conn = get_db()
    cur = conn.execute("SELECT balance FROM users WHERE user_id=? AND chat_id=?", (upd.effective_user.id, upd.effective_chat.id))
    r = cur.fetchone()
    if not r or r[0] < amt:
        conn.close()
        return await upd.message.reply_text("❌ Недостаточно запекоинов!")
    recv = int(amt * (1 - COMMISSION))
    comm = amt - recv
    conn.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND chat_id=?", (amt, upd.effective_user.id, upd.effective_chat.id))
    conn.execute("UPDATE users SET balance=balance+? WHERE user_id=? AND chat_id=?", (recv, t.id, upd.effective_chat.id))
    conn.commit()
    conn.close()
    await upd.message.reply_text(f"✅ Переведено {amt}. {t.first_name} получил {recv} (комиссия: {comm})")

def get_target(upd):
    """Возвращает пользователя из reply или @упоминания"""
    if upd.message.reply_to_message:
        return upd.message.reply_to_message.from_user
    if upd.message.entities:
        for e in upd.message.entities:
            if e.type == "text_mention" and e.user:
                return e.user
    return None

async def bal_command(upd, ctx):
    u, c = upd.effective_user, upd.effective_chat
    target = get_target(upd) or u
    du = get_user(target.id, c.id, target.username or "", target.first_name or "")
    if target.id == u.id:
        await upd.message.reply_text(f"🪙 Ваш баланс: {du['balance']} запекоинов")
    else:
        await upd.message.reply_text(f"🪙 Баланс {target.first_name}: {du['balance']} запекоинов")

async def send_trade_request(upd, ctx, target_user, amt, cost, item, is_buy):
    """Отправляет запрос на подтверждение сделки"""
    uid, cid = upd.effective_user.id, upd.effective_chat.id
    buyer_id = uid if is_buy else target_user.id
    seller_id = target_user.id if is_buy else uid
    item_name = "запеканок" if item == "c" else "сырников"
    prefix = "b" if is_buy else "s"
    key = f"trade:{cid}:{prefix}:{uid}:{target_user.id}"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Принять", callback_data=f"{prefix}_yes:{cid}:{buyer_id}:{seller_id}:{amt}:{cost}:{item}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"{prefix}_no:{cid}:{buyer_id}:{seller_id}:{amt}:{cost}:{item}"),
    ]])

    who = "купить" if is_buy else "продать"
    action = "продаёт" if is_buy else "покупает"
    from_name = upd.effective_user.first_name or upd.effective_user.username or str(uid)
    to_name = target_user.first_name or target_user.username or str(target_user.id)
    msg = f"@{upd.effective_user.first_name} хочет {who} {amt} {item_name} у @{target_user.first_name} за {cost} 🪙"
    await upd.message.reply_text(msg, reply_markup=kb)

async def cmd_buy(upd, ctx):
    s = get_target(upd)
    if not s:
        return await upd.message.reply_text("Ответьте на сообщение или укажите @продавца!")
    if s.id == upd.effective_user.id: return await upd.message.reply_text("Нельзя у себя!")
    if not ctx.args: return await upd.message.reply_text("Укажите количество!")
    try: amt = int(ctx.args[0])
    except: return await upd.message.reply_text("Число!")
    if amt <= 0: return await upd.message.reply_text("Положительное число!")
    cost = int(amt * C_PRICE)
    buyer = get_user(upd.effective_user.id, upd.effective_chat.id)
    seller = get_user(s.id, upd.effective_chat.id)
    if buyer["balance"] < cost: return await upd.message.reply_text(f"❌ Нужно {cost}, у вас {buyer['balance']}")
    if seller["casseroles"] < amt: return await upd.message.reply_text(f"❌ У продавца {seller['casseroles']}")
    await send_trade_request(upd, ctx, s, amt, cost, "c", True)

async def cmd_buy_s(upd, ctx):
    s = get_target(upd)
    if not s:
        return await upd.message.reply_text("Ответьте на сообщение или укажите @продавца!")
    if s.id == upd.effective_user.id: return await upd.message.reply_text("Нельзя у себя!")
    if not ctx.args: return await upd.message.reply_text("Укажите количество!")
    try: amt = int(ctx.args[0])
    except: return await upd.message.reply_text("Число!")
    if amt <= 0: return await upd.message.reply_text("Положительное число!")
    cost = int(amt * S_PRICE)
    buyer = get_user(upd.effective_user.id, upd.effective_chat.id)
    seller = get_user(s.id, upd.effective_chat.id)
    if buyer["balance"] < cost: return await upd.message.reply_text(f"❌ Нужно {cost}, у вас {buyer['balance']}")
    if seller["syrniki"] < amt: return await upd.message.reply_text(f"❌ У продавца {seller['syrniki']}")
    await send_trade_request(upd, ctx, s, amt, cost, "s", True)

async def cmd_sell(upd, ctx):
    b = get_target(upd)
    if not b:
        return await upd.message.reply_text("Ответьте на сообщение или укажите @покупателя!")
    if b.id == upd.effective_user.id: return await upd.message.reply_text("Нельзя себе!")
    if not ctx.args: return await upd.message.reply_text("Укажите количество!")
    try: amt = int(ctx.args[0])
    except: return await upd.message.reply_text("Число!")
    if amt <= 0: return await upd.message.reply_text("Положительное число!")
    cost = int(amt * C_PRICE)
    seller = get_user(upd.effective_user.id, upd.effective_chat.id)
    buyer = get_user(b.id, upd.effective_chat.id)
    if buyer["balance"] < cost: return await upd.message.reply_text(f"❌ У покупателя {buyer['balance']}, нужно {cost}")
    if seller["casseroles"] < amt: return await upd.message.reply_text(f"❌ У вас {seller['casseroles']}")
    await send_trade_request(upd, ctx, b, amt, cost, "c", False)

async def cmd_sell_s(upd, ctx):
    b = get_target(upd)
    if not b:
        return await upd.message.reply_text("Ответьте на сообщение или укажите @покупателя!")
    if b.id == upd.effective_user.id: return await upd.message.reply_text("Нельзя себе!")
    if not ctx.args: return await upd.message.reply_text("Укажите количество!")
    try: amt = int(ctx.args[0])
    except: return await upd.message.reply_text("Число!")
    if amt <= 0: return await upd.message.reply_text("Положительное число!")
    cost = int(amt * S_PRICE)
    seller = get_user(upd.effective_user.id, upd.effective_chat.id)
    buyer = get_user(b.id, upd.effective_chat.id)
    if buyer["balance"] < cost: return await upd.message.reply_text(f"❌ У покупателя {buyer['balance']}, нужно {cost}")
    if seller["syrniki"] < amt: return await upd.message.reply_text(f"❌ У вас {seller['syrniki']}")
    await send_trade_request(upd, ctx, b, amt, cost, "s", False)

pending = {}

async def cmd_coinflip(upd, ctx):
    op = get_target(upd)
    if not op:
        return await upd.message.reply_text("Ответьте на сообщение или укажите @противника!")
    if op.id == upd.effective_user.id: return await upd.message.reply_text("С собой нельзя!")
    if op.is_bot: return await upd.message.reply_text("С ботом нельзя!")
    if not ctx.args: return await upd.message.reply_text("Укажите ставку! /coinflip 100")
    try: bet = int(ctx.args[0])
    except: return await upd.message.reply_text("Число!")
    if bet <= 0: return await upd.message.reply_text("Положительное число!")
    u = get_user(upd.effective_user.id, upd.effective_chat.id)
    o = get_user(op.id, upd.effective_chat.id)
    if u["balance"] < bet: return await upd.message.reply_text(f"❌ У вас {u['balance']}, нужно {bet}")
    if o["balance"] < bet: return await upd.message.reply_text(f"❌ У {op.first_name} недостаточно!")
    key = f"{upd.effective_chat.id}:{upd.effective_user.id}:{op.id}"
    pending[key] = {"u1": upd.effective_user.id, "u2": op.id, "bet": bet, "mid": None}
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Принять", callback_data=f"cfa:{upd.effective_chat.id}:{upd.effective_user.id}:{op.id}:{bet}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"cfd:{upd.effective_chat.id}:{upd.effective_user.id}:{op.id}:{bet}"),
    ]])
    sent = await upd.message.reply_text(f"{op.first_name}, {upd.effective_user.first_name} вызывает вас!\nСтавка: {bet} 🪙", reply_markup=kb)
    pending[key]["mid"] = sent.message_id

async def coinflip_cb(upd, ctx):
    q = upd.callback_query
    await q.answer()
    _, cid, u1id, u2id, bet = q.data.split(":")
    cid, u1id, u2id, bet = int(cid), int(u1id), int(u2id), int(bet)
    if q.from_user.id != u2id: return await q.answer("Не ваш вызов!", show_alert=True)
    key = f"{cid}:{u1id}:{u2id}"
    if q.data.startswith("cfd"):
        await q.edit_message_text(f"❌ {q.from_user.first_name} отклонил вызов.")
        pending.pop(key, None)
        return
    if q.data.startswith("cfa"):
        pending.pop(key, None)
        du1 = get_user(u1id, cid)
        du2 = get_user(u2id, cid)
        if du1["balance"] < bet or du2["balance"] < bet:
            return await q.edit_message_text("❌ У кого-то не хватает денег!")
        f1, f2 = du1["first_name"] or du1["username"] or str(u1id), du2["first_name"] or du2["username"] or str(u2id)
        await q.edit_message_text(f"✅ @{q.from_user.first_name} принял вызов!")
        msg = await ctx.bot.send_message(cid, "3...")
        for i in [2, 1]:
            await asyncio.sleep(1)
            await msg.edit_text(f"{i}...")
        await asyncio.sleep(1)
        if random.choice([True, False]):
            winner, loser, wn, ln = u1id, u2id, f1, f2
        else:
            winner, loser, wn, ln = u2id, u1id, f2, f1
        conn = get_db()
        conn.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND chat_id=?", (bet, loser, cid))
        conn.execute("UPDATE users SET balance=balance+? WHERE user_id=? AND chat_id=?", (bet, winner, cid))
        conn.commit()
        conn.close()
        await msg.edit_text(f"🎉 Победил {wn}!\n💸 {ln} проиграл {bet} запекоинов.")

async def trade_cb(upd, ctx):
    q = upd.callback_query
    await q.answer()
    parts = q.data.split(":")
    action_prefix = parts[0]
    cid, buyer_id, seller_id = int(parts[1]), int(parts[2]), int(parts[3])
    amt, cost = int(parts[4]), int(parts[5])
    item = parts[6]

    is_buy = action_prefix.startswith("b")
    confirmer_id = seller_id if is_buy else buyer_id
    if q.from_user.id != confirmer_id:
        return await q.answer("Не ваша сделка!", show_alert=True)

    if action_prefix.endswith("_no"):
        await q.edit_message_text("❌ Сделка отклонена.")
        return

    du1 = get_user(buyer_id, cid)
    du2 = get_user(seller_id, cid)
    item_name = "запеканок" if item == "c" else "сырников"
    seller_stock = du2["casseroles"] if item == "c" else du2["syrniki"]
    buyer_stock_field = "casseroles" if item == "c" else "syrniki"

    if du1["balance"] < cost:
        return await q.edit_message_text("❌ У покупателя не хватает запекоинов!")
    if seller_stock < amt:
        return await q.edit_message_text(f"❌ У продавца не хватает {item_name}!")

    comm = int(cost * COMMISSION)
    sget = cost - comm
    upd_user(buyer_id, cid, balance=du1["balance"]-cost)
    upd_user(buyer_id, cid, **{buyer_stock_field: du1[buyer_stock_field]+amt})
    upd_user(seller_id, cid, balance=du2["balance"]+sget)
    upd_user(seller_id, cid, **{buyer_stock_field: du2[buyer_stock_field]-amt})

    action = "купил" if is_buy else "продал"
    await q.edit_message_text(f"✅ {q.from_user.first_name} {action} {amt} {item_name} за {cost} 🪙")

async def cmd_help(upd, ctx):
    await upd.message.reply_text(
        "🍳 <b>Запеканочный Бот</b>\n\n"
        "/casserole или «casserole» — замутить запеканки (раз в час)\n"
        "/me — профиль\n/balance — баланс (свой или @пользователя)\n/top — топ чата\n/top_syrniki — топ сырников\n"
        "/salary — зарплата 200 (раз в час)\n"
        "/givecoins N — перевести (комиссия 20%)\n"
        "/gift N — подарить запеканки\n"
        "/buy N — купить запеканки\n/buy_s N — купить сырники\n"
        "/sell N — продать запеканки\n/sell_s N — продать сырники\n"
        "/coinflip N — орёл и решка\n\n"
        "«подарить N» ответом — подарить запеканки\n\n"
        "10 запеканок = 25 🪙 | 1 сырник = 10 🪙\n"
        "Комиссия 20% на всё, кроме зарплаты и coinflip",
        parse_mode="HTML"
    )

async def text_casserole(upd, ctx):
    if not upd.message.text: return
    if upd.message.text.strip().lower() == "casserole":
        await cmd_casserole(upd, ctx)

async def text_gift(upd, ctx):
    if not upd.message.reply_to_message or not upd.message.text: return
    m = re.match(r'^подарить\s+(\d+)$', upd.message.text.strip(), re.I)
    if m:
        ctx.args = [m.group(1)]
        await cmd_gift(upd, ctx)

def main():
    init_db()
    builder = Application.builder().token(TOKEN)
    if PROXY:
        builder = builder.proxy_url(PROXY).get_updates_proxy_url(PROXY)
    app = builder.build()
    for cmd, fn in [("start", cmd_help), ("help", cmd_help), ("casserole", cmd_casserole), ("me", cmd_me),
                     ("balance", bal_command),
                     ("top", cmd_top), ("top_syrniki", cmd_top_syr), ("gift", cmd_gift),
                     ("salary", cmd_salary), ("givecoins", cmd_givecoins), ("buy", cmd_buy),
                     ("buy_s", cmd_buy_s), ("sell", cmd_sell), ("sell_s", cmd_sell_s),
                     ("coinflip", cmd_coinflip)]:
        app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(CallbackQueryHandler(coinflip_cb, pattern="^cf"))
    app.add_handler(CallbackQueryHandler(trade_cb, pattern="^[bs]_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.REPLY & filters.Regex(r'(?i)^casserole$'), text_casserole))
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, text_gift))
    print("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
