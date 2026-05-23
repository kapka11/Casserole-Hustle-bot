import asyncio, random, time, re, os, html, psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TOKEN = os.getenv('TELEGRAM_TOKEN')
DATABASE_URL = os.environ.get("DATABASE_URL", "")
COOLDOWN = 600
COMMISSION = 0.2
C_PRICE = 5
S_PRICE = 20
PROXY = os.environ.get("TG_PROXY", "")
STEAL_COOLDOWN = 300
STEAL_SUCCESS = 100

OWNER_ID = int(os.getenv('OWNER_ID', '0'))
ADMIN_IDS_RAW = os.getenv('ADMIN_IDS', '')
MODERATOR_IDS_RAW = os.getenv('MODERATOR_IDS', '')
TESTER_IDS_RAW = os.getenv('TESTER_IDS', '')

ROLES = {
    'owner': 4,
    'admin': 3,
    'moderator': 2,
    'tester': 1,
    'user': 0
}

def _parse_ids_env(raw_str, default_chat_id=0):
    assignments = []
    if not raw_str:
        return assignments
    parts = [p.strip() for p in raw_str.split(',') if p.strip()]
    for p in parts:
        if '@' in p:
            uid_str, target = p.split('@', 1)
            try:
                uid = int(uid_str.strip())
                target = target.strip().lower()
                if target == 'global':
                    chat_id = 0
                else:
                    chat_id = int(target)
                assignments.append( (uid, chat_id) )
            except ValueError:
                continue
        else:
            try:
                uid = int(p)
                assignments.append( (uid, default_chat_id) )
            except ValueError:
                continue
    return assignments

_env_admins = _parse_ids_env(ADMIN_IDS_RAW)
_env_mods = _parse_ids_env(MODERATOR_IDS_RAW)
_env_testers = _parse_ids_env(TESTER_IDS_RAW)

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT, chat_id BIGINT,
            username TEXT DEFAULT '', first_name TEXT DEFAULT '',
            total_casseroles INTEGER DEFAULT 0, casseroles INTEGER DEFAULT 0,
            total_syrniki INTEGER DEFAULT 0, syrniki INTEGER DEFAULT 0,
            casserole_actions INTEGER DEFAULT 0, level INTEGER DEFAULT 1,
            balance INTEGER DEFAULT 0,
            last_casserole REAL DEFAULT 0, last_salary REAL DEFAULT 0,
            next_level_at INTEGER DEFAULT 10,
            PRIMARY KEY (user_id, chat_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gsyrniki (
            user_id BIGINT PRIMARY KEY,
            username TEXT DEFAULT '', first_name TEXT DEFAULT '',
            total_syrniki INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS username_cache (
            username TEXT, chat_id BIGINT, user_id BIGINT,
            first_name TEXT DEFAULT '',
            PRIMARY KEY (username, chat_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS role_assignments (
            user_id BIGINT,
            chat_id BIGINT DEFAULT 0,
            role TEXT NOT NULL,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, chat_id)
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def _get_role_from_env(uid, cid):
    if uid == OWNER_ID:
        return 'owner'
    
    for (user_id, chat_id) in _env_admins:
        if user_id == uid and (chat_id == cid or chat_id == 0):
            if chat_id == cid: return 'admin'
            
    for (user_id, chat_id) in _env_mods:
        if user_id == uid and (chat_id == cid or chat_id == 0):
            if chat_id == cid: return 'moderator'
            
    for (user_id, chat_id) in _env_testers:
        if user_id == uid and (chat_id == cid or chat_id == 0):
            if chat_id == cid: return 'tester'
            
    for (user_id, chat_id) in _env_admins:
        if user_id == uid and chat_id == 0:
            return 'admin'
    for (user_id, chat_id) in _env_mods:
        if user_id == uid and chat_id == 0:
            return 'moderator'
    for (user_id, chat_id) in _env_testers:
        if user_id == uid and chat_id == 0:
            return 'tester'

    return None

def _get_role_from_db(uid, cid):
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT role FROM role_assignments WHERE user_id=%s AND chat_id=%s", (uid, cid))
    r_local = cur.fetchone()
    if r_local:
        cur.close(); conn.close()
        return r_local[0]
    
    cur.execute("SELECT role FROM role_assignments WHERE user_id=%s AND chat_id=0", (uid,))
    r_global = cur.fetchone()
    if r_global:
        cur.close(); conn.close()
        return r_global[0]
    
    cur.close(); conn.close()
    return None

def get_effective_role(uid, cid):
    if uid == OWNER_ID:
        return 'owner'
    
    role_db_local = _get_role_from_db(uid, cid)
    if role_db_local:
        return role_db_local
    
    role_env_local = None
    if uid in [x[0] for x in _env_admins if x[1] == cid]: role_env_local = 'admin'
    elif uid in [x[0] for x in _env_mods if x[1] == cid]: role_env_local = 'moderator'
    elif uid in [x[0] for x in _env_testers if x[1] == cid]: role_env_local = 'tester'
    
    if role_env_local:
        return role_env_local

    role_db_global = _get_role_from_db(uid, 0)
    if role_db_global:
        return role_db_global

    role_env_global = _get_role_from_env(uid, cid)
    if role_env_global:
        return role_env_global

    return 'user'

def is_owner(uid, cid=None):
    return uid == OWNER_ID

def is_admin(uid, cid):
    role = get_effective_role(uid, cid)
    return ROLES[role] >= ROLES['admin']

def is_moderator(uid, cid):
    role = get_effective_role(uid, cid)
    return ROLES[role] >= ROLES['moderator']

def is_tester(uid, cid):
    role = get_effective_role(uid, cid)
    return ROLES[role] >= ROLES['tester']

def can_interact(actor_uid, target_uid, cid):
    if actor_uid == target_uid: return False
    if is_owner(actor_uid): return True
    if is_owner(target_uid): return False
    
    actor_role = get_effective_role(actor_uid, cid)
    target_role = get_effective_role(target_uid, cid)
    
    return ROLES[actor_role] > ROLES[target_role]

COLUMNS = ["user_id","chat_id","username","first_name","total_casseroles","casseroles","total_syrniki","syrniki","casserole_actions","level","balance","last_casserole","last_salary","next_level_at"]

def row_dict(row, cols=COLUMNS):
    return dict(zip(cols, row)) if row else None

def get_user(uid, cid, uname="", fname=""):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=%s AND chat_id=%s", (uid, cid))
    row = cur.fetchone()
    if row:
        if uname or fname:
            cur.execute("UPDATE users SET username=%s, first_name=%s WHERE user_id=%s AND chat_id=%s", (uname, fname, uid, cid))
            conn.commit()
        cur.close()
        conn.close()
        return row_dict(row)
    req = random.randint(5, 10)
    cur.execute("INSERT INTO users (user_id,chat_id,username,first_name,next_level_at) VALUES (%s,%s,%s,%s,%s)", (uid, cid, uname, fname, req))
    conn.commit()
    cur.close()
    conn.close()
    return {"user_id":uid,"chat_id":cid,"username":uname,"first_name":fname,"total_casseroles":0,"casseroles":0,"total_syrniki":0,"syrniki":0,"casserole_actions":0,"level":1,"balance":0,"last_casserole":0,"last_salary":0,"next_level_at":req}

def upd_user(uid, cid, **kw):
    conn = get_db()
    cur = conn.cursor()
    sets = ", ".join(f"{k}=%s" for k in kw)
    vals = list(kw.values()) + [uid, cid]
    cur.execute(f"UPDATE users SET {sets} WHERE user_id=%s AND chat_id=%s", vals)
    conn.commit()
    cur.close()
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
        cur = conn.cursor()
        cur.execute("SELECT total_syrniki FROM gsyrniki WHERE user_id=%s", (uid,))
        r = cur.fetchone()
        if r:
            cur.execute("UPDATE gsyrniki SET total_syrniki=%s, username=%s, first_name=%s WHERE user_id=%s", (r[0]+syr, uname, fname, uid))
        else:
            cur.execute("INSERT INTO gsyrniki (user_id,username,first_name,total_syrniki) VALUES (%s,%s,%s,%s)", (uid, uname, fname, syr))
        conn.commit()
        cur.close()
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

async def cmd_profile(upd, ctx):
    u, c = upd.effective_user, upd.effective_chat
    target = await get_target(upd, ctx) or u
    du = get_user(target.id, c.id, target.username or "", target.first_name or "")
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*)+1 FROM users WHERE chat_id=%s AND total_casseroles>(SELECT total_casseroles FROM users WHERE user_id=%s AND chat_id=%s)", (c.id, target.id, c.id))
    chat_rank = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*)+1 FROM users WHERE total_casseroles>(SELECT total_casseroles FROM users WHERE user_id=%s AND chat_id=%s)", (target.id, c.id))
    global_rank = cur.fetchone()[0]
    cur.close(); conn.close()
    rem = du["next_level_at"] - du["casserole_actions"]
    name = target.first_name or target.username or f"#{target.id}"
    mention = f' (@{target.username})' if getattr(target, 'username', None) else ''
    await upd.message.reply_text(
        f"👤 {name}{mention}\n"
        f"🍳 Запеканок: {du['total_casseroles']} (в наличии: {du['casseroles']})\n"
        f"🔢 Замутов сделано: {du['casserole_actions']}\n"
        f"🧀 Сырников получено: {du['total_syrniki']} (в наличии: {du['syrniki']})\n"
        f"🪙 Запекоинов: {du['balance']}\n"
        f"📊 Уровень: {du['level']}\n"
        f"🎯 Осталось замуток до нового уровня: {rem}\n"
        f"🏆 Место в топе чата: {chat_rank}\n"
        f"🌍 Ранг в боте: {global_rank}"
    )

async def cmd_top(upd, ctx):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE chat_id=%s ORDER BY total_casseroles DESC LIMIT 10", (upd.effective_chat.id,))
    c1 = cur.fetchall()
    cur.execute("SELECT * FROM users WHERE chat_id=%s ORDER BY level DESC LIMIT 10", (upd.effective_chat.id,))
    c2 = cur.fetchall()
    cur.close()
    conn.close()
    msg = "🏆 <b>ТОП ЗАПЕКАНОЧНЫХ ЦЕНТРОВ</b>\n\n<b>По запеканкам:</b>\n"
    for i, r in enumerate(c1, 1):
        rd = row_dict(r)
        msg += f"{i}. {rd['first_name'] or rd['username'] or '#'+str(rd['user_id'])} — {rd['total_casseroles']} 🍳\n"
    msg += "\n<b>По уровню:</b>\n"
    for i, r in enumerate(c2, 1):
        rd = row_dict(r)
        msg += f"{i}. {rd['first_name'] or rd['username'] or '#'+str(rd['user_id'])} — {rd['level']} 🆙\n"
    await upd.message.reply_text(msg, parse_mode="HTML")

async def cmd_top_syr(upd, ctx):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM gsyrniki WHERE total_syrniki>0 ORDER BY total_syrniki DESC LIMIT 10")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    msg = "🧀 <b>ТОП ВЕЗУНЧИКОВ (ВСЕ ЧАТЫ)</b>\n\n"
    for i, r in enumerate(rows, 1):
        rd = row_dict(r, ["user_id","username","first_name","total_syrniki"])
        msg += f"{i}. {rd['first_name'] or rd['username'] or '#'+str(rd['user_id'])} — {rd['total_syrniki']} 🧀\n"
    await upd.message.reply_text(msg or "Пока никого нет", parse_mode="HTML")

async def cmd_gift(upd, ctx):
    t = await get_target(upd, ctx)
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
    cur = conn.cursor()
    cur.execute("SELECT casseroles FROM users WHERE user_id=%s AND chat_id=%s", (upd.effective_user.id, upd.effective_chat.id))
    r = cur.fetchone()
    if not r or r[0] < amt:
        cur.close()
        conn.close()
        return await upd.message.reply_text("❌ Недостаточно запеканок!")
    cur.execute("UPDATE users SET casseroles=casseroles-%s WHERE user_id=%s AND chat_id=%s", (amt, upd.effective_user.id, upd.effective_chat.id))
    cur.execute("UPDATE users SET casseroles=casseroles+%s WHERE user_id=%s AND chat_id=%s", (amt, t.id, upd.effective_chat.id))
    conn.commit()
    cur.close()
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
    t = await get_target(upd, ctx)
    if not t:
        return await upd.message.reply_text("Ответьте на сообщение или укажите @пользователя!")
    if t.id == upd.effective_user.id:
        return await upd.message.reply_text("Себе нельзя!")
    if not ctx.args: return await upd.message.reply_text("Укажите сумму! /givecoins 100")
    try: amt = int(ctx.args[0])
    except: return await upd.message.reply_text("Число!")
    if amt <= 0: return await upd.message.reply_text("Положительное число!")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=%s AND chat_id=%s", (upd.effective_user.id, upd.effective_chat.id))
    r = cur.fetchone()
    if not r or r[0] < amt:
        cur.close()
        conn.close()
        return await upd.message.reply_text("❌ Недостаточно запекоинов!")
    recv = int(amt * (1 - COMMISSION))
    comm = amt - recv
    cur.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s AND chat_id=%s", (amt, upd.effective_user.id, upd.effective_chat.id))
    cur.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s AND chat_id=%s", (recv, t.id, upd.effective_chat.id))
    conn.commit()
    cur.close()
    conn.close()
    await upd.message.reply_text(f"✅ Переведено {amt}. {t.first_name} получил {recv} (комиссия: {comm})")

async def get_target(upd, ctx):
    if upd.message.entities:
        for e in upd.message.entities:
            if e.type == "text_mention" and e.user:
                return e.user
            if e.type == "mention":
                username = upd.message.parse_entity(e).lstrip("@")
                try:
                    chat = await ctx.bot.get_chat(f"@{username}")
                    return chat
                except:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT user_id, first_name FROM username_cache WHERE LOWER(username)=%s AND chat_id=%s LIMIT 1", (username.lower(), upd.effective_chat.id))
                    r = cur.fetchone()
                    cur.close()
                    conn.close()
                    if r:
                        class Fake: pass
                        u = Fake()
                        u.id = r[0]; u.first_name = r[1] or username; u.username = username
                        return u
    if ctx.args:
        for i, arg in enumerate(ctx.args):
            if arg.startswith("@"):
                username = arg.lstrip("@")
                try:
                    chat = await ctx.bot.get_chat(f"@{username}")
                    ctx.args.pop(i)
                    return chat
                except:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT user_id, first_name FROM username_cache WHERE LOWER(username)=%s AND chat_id=%s LIMIT 1", (username.lower(), upd.effective_chat.id))
                    r = cur.fetchone()
                    cur.close()
                    conn.close()
                    if r:
                        class Fake: pass
                        u = Fake()
                        u.id = r[0]; u.first_name = r[1] or username; u.username = username
                        ctx.args.pop(i)
                        return u
    if upd.message.reply_to_message:
        return upd.message.reply_to_message.from_user
    return None

async def bal_command(upd, ctx):
    u, c = upd.effective_user, upd.effective_chat
    target = await get_target(upd, ctx) or u
    du = get_user(target.id, c.id, target.username or "", target.first_name or "")
    if target.id == u.id:
        await upd.message.reply_text(f"🪙 Ваш баланс: {du['balance']} запекоинов")
    else:
        await upd.message.reply_text(f"🪙 Баланс {target.first_name}: {du['balance']} запекоинов")

async def send_trade_request(upd, ctx, target_user, amt, cost, item, is_buy):
    uid, cid = upd.effective_user.id, upd.effective_chat.id
    buyer_id = uid if is_buy else target_user.id
    seller_id = target_user.id if is_buy else uid
    item_name = "запеканок" if item == "c" else "сырников"
    prefix = "b" if is_buy else "s"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Принять", callback_data=f"{prefix}_yes:{cid}:{buyer_id}:{seller_id}:{amt}:{cost}:{item}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"{prefix}_no:{cid}:{buyer_id}:{seller_id}:{amt}:{cost}:{item}"),
    ]])
    who = "купить" if is_buy else "продать"
    msg = f"@{upd.effective_user.first_name} хочет {who} {amt} {item_name} у @{target_user.first_name} за {cost} 🪙"
    await upd.message.reply_text(msg, reply_markup=kb)

async def cmd_buy(upd, ctx):
    s = await get_target(upd, ctx)
    if not s: return await upd.message.reply_text("Ответьте на сообщение или укажите @продавца!")
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
    s = await get_target(upd, ctx)
    if not s: return await upd.message.reply_text("Ответьте на сообщение или укажите @продавца!")
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
    b = await get_target(upd, ctx)
    if not b: return await upd.message.reply_text("Ответьте на сообщение или укажите @покупателя!")
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
    b = await get_target(upd, ctx)
    if not b: return await upd.message.reply_text("Ответьте на сообщение или укажите @покупателя!")
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
    op = await get_target(upd, ctx)
    if not op: return await upd.message.reply_text("Ответьте на сообщение или укажите @противника!")
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
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s AND chat_id=%s", (bet, loser, cid))
        cur.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s AND chat_id=%s", (bet, winner, cid))
        conn.commit()
        cur.close()
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

async def stealing(upd, ctx):
    uid = upd.effective_user.id
    cid = upd.effective_chat.id
    if not is_admin(uid, cid): return
    first = upd.effective_user.first_name
    
    if not ctx.args: return await upd.message.reply_text("Укажите количество!")
    try: amt = int(ctx.args[0])
    except: return await upd.message.reply_text("Число!")
    if amt <= 0: return await upd.message.reply_text("Положительное число!")
    
    t, msg_start = None, 1
    if len(ctx.args) > 1 and ctx.args[1].startswith("@"):
        username = ctx.args[1].lstrip("@")
        try:
            t = await ctx.bot.get_chat(f"@{username}")
        except:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT user_id, first_name FROM username_cache WHERE LOWER(username)=%s AND chat_id=%s LIMIT 1", (username.lower(), cid))
            r = cur.fetchone(); cur.close(); conn.close()
            if r:
                class Fake: pass
                t = Fake(); t.id = r[0]; t.first_name = r[1] or username; t.username = username
        msg_start = 2 if t else 1
    if not t and upd.message.reply_to_message:
        t = upd.message.reply_to_message.from_user
        msg_start = 1
    if not t or t.id == uid: return await upd.message.reply_text("Укажите @жертву!")
    
    if not can_interact(uid, t.id, cid): 
        return await upd.message.reply_text("❌ Нельзя красть у пользователя с более высоким или равным рангом!")

    thief = get_user(uid, cid); victim = get_user(t.id, cid)
    if victim["casseroles"] < amt: return await upd.message.reply_text(f"❌ У жертвы {victim['casseroles']}")
    upd_user(uid, cid, casseroles=thief["casseroles"]+amt)
    upd_user(t.id, cid, casseroles=victim["casseroles"]-amt)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*)+1 FROM users WHERE chat_id=%s AND casseroles>(SELECT casseroles FROM users WHERE user_id=%s AND chat_id=%s)", (cid, t.id, cid))
    rank = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE chat_id=%s", (cid,))
    total = cur.fetchone()[0]; cur.close(); conn.close()
    remained = victim["casseroles"] - amt
    rest = " ".join(ctx.args[msg_start:]) if len(ctx.args) > msg_start else ""
    comment = f'\n<blockquote>{html.escape(rest)}</blockquote>' if rest else ""
    await ctx.bot.send_message(chat_id=cid, text=f"✨ {t.first_name} 🤞 {first} спиздил у тебя {amt} запеканок!\nТеперь у тебя {remained}🍪\n📊Место в рейтинге {rank}/{total}{comment}", parse_mode="HTML")
    await upd.message.delete()

async def stealing_s(upd, ctx):
    uid = upd.effective_user.id
    cid = upd.effective_chat.id
    if not is_admin(uid, cid): return
    first = upd.effective_user.first_name
    
    if not ctx.args: return await upd.message.reply_text("Укажите количество!")
    try: amt = int(ctx.args[0])
    except: return await upd.message.reply_text("Число!")
    if amt <= 0: return await upd.message.reply_text("Положительное число!")
    
    t, msg_start = None, 1
    if len(ctx.args) > 1 and ctx.args[1].startswith("@"):
        username = ctx.args[1].lstrip("@")
        try:
            t = await ctx.bot.get_chat(f"@{username}")
        except:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT user_id, first_name FROM username_cache WHERE LOWER(username)=%s AND chat_id=%s LIMIT 1", (username.lower(), cid))
            r = cur.fetchone(); cur.close(); conn.close()
            if r:
                class Fake: pass
                t = Fake(); t.id = r[0]; t.first_name = r[1] or username; t.username = username
        msg_start = 2 if t else 1
    if not t and upd.message.reply_to_message:
        t = upd.message.reply_to_message.from_user
        msg_start = 1
    if not t or t.id == uid: return await upd.message.reply_text("Укажите @жертву!")
    
    if not can_interact(uid, t.id, cid): 
        return await upd.message.reply_text("❌ Нельзя красть у пользователя с более высоким или равным рангом!")

    thief = get_user(uid, cid); victim = get_user(t.id, cid)
    if victim["syrniki"] < amt: return await upd.message.reply_text(f"❌ У жертвы {victim['syrniki']}")
    upd_user(uid, cid, syrniki=thief["syrniki"]+amt)
    upd_user(t.id, cid, syrniki=victim["syrniki"]-amt)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*)+1 FROM users WHERE chat_id=%s AND syrniki>(SELECT syrniki FROM users WHERE user_id=%s AND chat_id=%s)", (cid, t.id, cid))
    rank = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE chat_id=%s", (cid,))
    total = cur.fetchone()[0]; cur.close(); conn.close()
    remained = victim["syrniki"] - amt
    rest = " ".join(ctx.args[msg_start:]) if len(ctx.args) > msg_start else ""
    comment = f'\n<blockquote>{html.escape(rest)}</blockquote>' if rest else ""
    await ctx.bot.send_message(chat_id=cid, text=f"✨ {t.first_name} 🤞 {first} спиздил у тебя {amt} сырников!\nТеперь у тебя {remained}🧀\n📊Место в рейтинге {rank}/{total}{comment}", parse_mode="HTML")
    await upd.message.delete()

async def stealing_coins(upd, ctx):
    uid = upd.effective_user.id
    cid = upd.effective_chat.id
    if not is_admin(uid, cid): return
    first = upd.effective_user.first_name
    
    if not ctx.args: return await upd.message.reply_text("Укажите количество!")
    try: amt = int(ctx.args[0])
    except: return await upd.message.reply_text("Число!")
    if amt <= 0: return await upd.message.reply_text("Положительное число!")
    
    t, msg_start = None, 1
    if len(ctx.args) > 1 and ctx.args[1].startswith("@"):
        username = ctx.args[1].lstrip("@")
        try:
            t = await ctx.bot.get_chat(f"@{username}")
        except:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT user_id, first_name FROM username_cache WHERE LOWER(username)=%s AND chat_id=%s LIMIT 1", (username.lower(), cid))
            r = cur.fetchone(); cur.close(); conn.close()
            if r:
                class Fake: pass
                t = Fake(); t.id = r[0]; t.first_name = r[1] or username; t.username = username
        msg_start = 2 if t else 1
    if not t and upd.message.reply_to_message:
        t = upd.message.reply_to_message.from_user
        msg_start = 1
    if not t or t.id == uid: return await upd.message.reply_text("Укажите @жертву!")
    
    if not can_interact(uid, t.id, cid): 
        return await upd.message.reply_text("❌ Нельзя красть у пользователя с более высоким или равным рангом!")

    thief = get_user(uid, cid); victim = get_user(t.id, cid)
    if victim["balance"] < amt: return await upd.message.reply_text(f"❌ У жертвы {victim['balance']}")
    upd_user(uid, cid, balance=thief["balance"]+amt)
    upd_user(t.id, cid, balance=victim["balance"]-amt)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*)+1 FROM users WHERE chat_id=%s AND balance>(SELECT balance FROM users WHERE user_id=%s AND chat_id=%s)", (cid, t.id, cid))
    rank = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE chat_id=%s", (cid,))
    total = cur.fetchone()[0]; cur.close(); conn.close()
    remained = victim["balance"] - amt
    rest = " ".join(ctx.args[msg_start:]) if len(ctx.args) > msg_start else ""
    comment = f'\n<blockquote>{html.escape(rest)}</blockquote>' if rest else ""
    await ctx.bot.send_message(chat_id=cid, text=f"✨ {t.first_name} 🤞 {first} спиздил у тебя {amt} запекоинов!\nТеперь у тебя {remained}🪙\n📊Место в рейтинге {rank}/{total}{comment}", parse_mode="HTML")
    await upd.message.delete()

async def _get_user_by_arg(ctx, cid, arg):
    if not arg.startswith("@"):
        return None
    username = arg.lstrip("@")
    t = None
    try:
        t = await ctx.bot.get_chat(f"@{username}")
    except:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT user_id, first_name FROM username_cache WHERE LOWER(username)=%s AND chat_id=%s LIMIT 1", (username.lower(), cid))
        r = cur.fetchone(); cur.close(); conn.close()
        if r:
            class Fake: pass
            t = Fake(); t.id = r[0]; t.first_name = r[1] or username; t.username = username
    return t

async def _transfer_logic(upd, ctx, item_field, item_name):
    uid = upd.effective_user.id
    cid = upd.effective_chat.id
    if not is_moderator(uid, cid): return
    
    if not ctx.args or len(ctx.args) < 3:
        return await upd.message.reply_text(f"Формат: /transfer N @от_кого @кому")
    
    try: amt = int(ctx.args[0])
    except: return await upd.message.reply_text("Количество должно быть числом!")
    
    if amt <= 0: return await upd.message.reply_text("Количество должно быть положительным!")
    
    t_from = await _get_user_by_arg(ctx, cid, ctx.args[1])
    t_to = await _get_user_by_arg(ctx, cid, ctx.args[2])

    if not t_from:
        return await upd.message.reply_text(f"Не могу найти отправителя {ctx.args[1]}")
    if not t_to:
        return await upd.message.reply_text(f"Не могу найти получателя {ctx.args[2]}")

    if t_from.id == t_to.id:
        return await upd.message.reply_text("Нельзя передать самому себе!")

    if not can_interact(uid, t_from.id, cid):
        return await upd.message.reply_text("❌ Нельзя передавать от имени пользователя с более высоким или равным рангом!")

    u_from = get_user(t_from.id, cid)
    u_to = get_user(t_to.id, cid, t_to.username or "", t_to.first_name or "")

    if u_from[item_field] < amt:
        return await upd.message.reply_text(f"❌ У {t_from.first_name} недостаточно {item_name}!")

    upd_user(t_from.id, cid, **{item_field: u_from[item_field] - amt})
    upd_user(t_to.id, cid, **{item_field: u_to[item_field] + amt})

    await upd.message.reply_text(
        f"✅ Передано {amt} {item_name}\n"
        f"От: {t_from.first_name} (@{t_from.username if hasattr(t_from, 'username') and t_from.username else '#'+str(t_from.id)})\n"
        f"Кому: {t_to.first_name} (@{t_to.username if hasattr(t_to, 'username') and t_to.username else '#'+str(t_to.id)})"
    )

async def cmd_transfer(upd, ctx):
    await _transfer_logic(upd, ctx, "casseroles", "запеканок")

async def cmd_transfer_s(upd, ctx):
    await _transfer_logic(upd, ctx, "syrniki", "сырников")

async def cmd_transfer_coins(upd, ctx):
    await _transfer_logic(upd, ctx, "balance", "запекоинов")

async def _assign_role_logic(upd, ctx, role, is_remove):
    uid = upd.effective_user.id
    cid = upd.effective_chat.id
    
    actor_role = get_effective_role(uid, cid)
    
    if role == 'owner':
        return await upd.message.reply_text("❌ Нельзя назначать или увольнять владельца через команды.")
    
    if role not in ROLES:
        return await upd.message.reply_text("❌ Неизвестная роль. Используйте: admin, moderator, tester")

    t = await get_target(upd, ctx)
    if not t: 
        if is_remove:
            return await upd.message.reply_text("Формат: /delrole [role] @user [local/global]")
        return await upd.message.reply_text("Формат: /addrole [role] @user [local/global]")
    
    if actor_role == 'admin' and role == 'admin':
         return await upd.message.reply_text("❌ Администратор не может назначать других администраторов.")
    
    if ROLES[actor_role] <= ROLES[role]:
         return await upd.message.reply_text("❌ Нельзя назначать роль выше или равную вашей.")

    target_chat_id = cid
    target_chat_name = "в этом чате (локально)"
    is_global = False
    
    if ctx.args and len(ctx.args) > 0:
        last_arg = ctx.args[-1].lower().strip()
        if last_arg == 'global':
            is_global = True
            if not is_owner(uid):
                return await upd.message.reply_text("❌ Только владелец может назначать/увольнять глобальные роли.")
            target_chat_id = 0
            target_chat_name = "глобально"

    if is_remove:
        action_name = "удалён из"
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM role_assignments WHERE user_id=%s AND chat_id=%s", (t.id, target_chat_id))
        conn.commit(); cur.close(); conn.close()
    else:
        action_name = "назначен"
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO role_assignments (user_id, chat_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, chat_id) DO UPDATE SET role=%s", (t.id, target_chat_id, role, role))
        conn.commit(); cur.close(); conn.close()

    await upd.message.reply_text(f"✅ {t.first_name} {action_name} {role} {target_chat_name}!")

async def cmd_addrole(upd, ctx):
    if not ctx.args or len(ctx.args) < 1:
        return await upd.message.reply_text("Формат: /addrole [admin/moderator/tester] @user [local/global]")
    
    target_role = ctx.args[0].lower().strip()
    await _assign_role_logic(upd, ctx, target_role, is_remove=False)

async def cmd_delrole(upd, ctx):
    if not ctx.args or len(ctx.args) < 1:
        return await upd.message.reply_text("Формат: /delrole [admin/moderator/tester] @user [local/global]")
    
    target_role = ctx.args[0].lower().strip()
    await _assign_role_logic(upd, ctx, target_role, is_remove=True)

async def cmd_addadmin(upd, ctx):
    ctx.args.insert(0, 'moderator')
    await cmd_addrole(upd, ctx)

async def cmd_deladmin(upd, ctx):
    ctx.args.insert(0, 'moderator')
    await cmd_delrole(upd, ctx)

async def cmd_listadmins(upd, ctx):
    uid = upd.effective_user.id
    cid = upd.effective_chat.id
    
    if not is_moderator(uid, cid): return
    
    conn = get_db(); cur = conn.cursor()
    
    cur.execute("SELECT user_id, role, chat_id FROM role_assignments")
    all_db_roles = cur.fetchall()
    cur.close()
    conn.close()
    
    my_role = get_effective_role(uid, cid)
    
    msg_lines = ["<b>⚙️ Роли в боте:</b>"]
    
    msg_lines.append(f"\n<b>Ранг этого чата ({cid}):</b>")
    msg_lines.append(f"👑 Владелец: <code>{OWNER_ID}</code>")
    
    local_roles_db = [(uid, role) for uid, role, chat_id in all_db_roles if chat_id == cid]
    global_roles_db = [(uid, role) for uid, role, chat_id in all_db_roles if chat_id == 0]
    
    local_mods_env = [x[0] for x in _env_mods if x[1] == cid]
    local_admins_env = [x[0] for x in _env_admins if x[1] == cid]
    local_testers_env = [x[0] for x in _env_testers if x[1] == cid]
    global_mods_env = [x[0] for x in _env_mods if x[1] == 0]
    global_admins_env = [x[0] for x in _env_admins if x[1] == 0]
    global_testers_env = [x[0] for x in _env_testers if x[1] == 0]
    
    msg_lines.append(f"\n🔧 <b>Тестеры (Локальные, БД):</b> " + (", ".join(f"<code>{uid}</code>" for uid, role in local_roles_db if role=='tester') if local_roles_db else "нет"))
    msg_lines.append(f"🛡️ <b>Модераторы (Локальные, БД):</b> " + (", ".join(f"<code>{uid}</code>" for uid, role in local_roles_db if role=='moderator') if local_roles_db else "нет"))
    msg_lines.append(f"⚡ <b>Админы (Локальные, БД):</b> " + (", ".join(f"<code>{uid}</code>" for uid, role in local_roles_db if role=='admin') if local_roles_db else "нет"))
    
    if local_mods_env or local_admins_env or local_testers_env:
        msg_lines.append(f"\n<b>(Из переменных окружения для этого чата):</b>")
        if local_admins_env: msg_lines.append(f"⚡ ENV Admins: {', '.join(map(str, local_admins_env))}")
        if local_mods_env: msg_lines.append(f"🛡️ ENV Mods: {', '.join(map(str, local_mods_env))}")
        if local_testers_env: msg_lines.append(f"🔧 ENV Testers: {', '.join(map(str, local_testers_env))}")

    msg_lines.append(f"\n🌍 <b>Глобальные роли (БД):</b>")
    msg_lines.append(f"⚡ Глобальные Админы: " + (", ".join(f"<code>{uid}</code>" for uid, role in global_roles_db if role=='admin') if global_roles_db else "нет"))
    msg_lines.append(f"🛡️ Глобальные Модераторы: " + (", ".join(f"<code>{uid}</code>" for uid, role in global_roles_db if role=='moderator') if global_roles_db else "нет"))
    msg_lines.append(f"🔧 Глобальные Тестеры: " + (", ".join(f"<code>{uid}</code>" for uid, role in global_roles_db if role=='tester') if global_roles_db else "нет"))
    
    if global_admins_env or global_mods_env or global_testers_env:
         msg_lines.append(f"\n<b>(Из глобальных переменных окружения):</b>")
         if global_admins_env: msg_lines.append(f"⚡ ENV Admins: {', '.join(map(str, global_admins_env))}")
         if global_mods_env: msg_lines.append(f"🛡️ ENV Mods: {', '.join(map(str, global_mods_env))}")
         if global_testers_env: msg_lines.append(f"🔧 ENV Testers: {', '.join(map(str, global_testers_env))}")

    msg_lines.append(f"\n🔍 Ваш ранг здесь: <b>{my_role}</b>")
    
    msg = "\n".join(msg_lines)
    await upd.message.reply_text(msg, parse_mode="HTML")

async def cmd_myid(upd, ctx):
    uid = upd.effective_user.id
    cid = upd.effective_chat.id
    
    if not is_tester(uid, cid): return
    
    msg = f"Ваш ID: <code>{uid}</code>\nID чата: <code>{cid}</code>"
    
    if upd.message.reply_to_message:
        target = upd.message.reply_to_message.from_user
        msg += f"\n\nID пользователя (ответ): {target.first_name}\nID: <code>{target.id}</code>"
        if target.username:
            msg += f"\nUsername: @{target.username}"
    
    await upd.message.reply_text(msg, parse_mode="HTML")

async def cmd_stickerid(upd, ctx):
    uid = upd.effective_user.id
    cid = upd.effective_chat.id
    
    if not is_tester(uid, cid): return
    
    if not upd.message.reply_to_message or not upd.message.reply_to_message.sticker:
        return await upd.message.reply_text("Ответьте этой командой на стикер!")
    
    sticker = upd.message.reply_to_message.sticker
    msg = (
        f"🖼️ ID стикера: <code>{sticker.file_id}</code>\n"
        f"Уникальный ID: <code>{sticker.file_unique_id}</code>\n"
        f"Набор: {sticker.set_name or 'Индивидуальный'}"
    )
    if sticker.emoji:
        msg += f"\nЭмодзи: {sticker.emoji}"
    
    await upd.message.reply_text(msg, parse_mode="HTML")

async def cmd_info(upd, ctx):
    uid = upd.effective_user.id
    cid = upd.effective_chat.id
    
    if not is_tester(uid, cid): return
    
    m = upd.message
    info_lines = []
    
    info_lines.append(f"Message ID: <code>{m.message_id}</code>")
    
    if m.reply_to_message:
        rm = m.reply_to_message
        info_lines.append(f"\n--- Ответ на сообщение ---")
        info_lines.append(f"From ID: <code>{rm.from_user.id}</code>")
        if rm.forward_from:
            info_lines.append(f"Forward from: <code>{rm.forward_from.id}</code> ({rm.forward_from.first_name})")
        if rm.forward_from_chat:
            info_lines.append(f"Forward from chat: <code>{rm.forward_from_chat.id}</code> ({rm.forward_from_chat.title})")
        if rm.new_chat_members:
            for u in rm.new_chat_members:
                info_lines.append(f"New member: <code>{u.id}</code> ({u.first_name})")
        if rm.left_chat_member:
            info_lines.append(f"Left member: <code>{rm.left_chat_member.id}</code> ({rm.left_chat_member.first_name})")
    
    msg = "\n".join(info_lines)
    await upd.message.reply_text(msg or "Нет особой информации", parse_mode="HTML")

async def cmd_start(upd, ctx):
    uid = upd.effective_user.id
    cid = upd.effective_chat.id
    
    user_cmds = (
        "👤 <b>Пользователь:</b>\n"
        "/profile — профиль\n/balance — баланс\n/top — топ чата\n/top_syrniki — топ сырников\n"
        "/casserole — замутить запеканки\n/salary — зарплата\n"
        "/gift N — подарить запеканки\n/givecoins N — перевести запекоины\n"
        "/buy N, /sell N — купить/продать запеканки\n"
        "/buy_s N, /sell_s N — купить/продать сырники\n"
        "/coinflip N — орёл и решка"
    )
    tester_cmds = "\n\n🔧 <b>Тестер:</b>\n/id — узнать свои ID\n/get_sticker_id — узнать ID стикера\n/msg_info — отладка сообщения"
    mod_cmds = "\n\n🛡️ <b>Модератор:</b>\n/transfer N @from @to — передать запеканки\n/transfer_s N @from @to — передать сырники\n/transfer_coins N @from @to — передать запекоины\n/listadmins — список ролей"
    admin_cmds = "\n\n⚡ <b>Админ:</b>\n/stealing N — украсть запеканки\n/stealing_s N — украсть сырники\n/stealing_coins N — украсть запекоины\n/addrole [tester/moderator] @user [local|global] — назначить роль\n/delrole [tester/moderator] @user [local|global] — уволить"
    
    msg = f"🍳 <b>Запеканочный Бот</b>\n\n{user_cmds}"
    if is_tester(uid, cid): msg += tester_cmds
    if is_moderator(uid, cid): msg += mod_cmds
    if is_admin(uid, cid): msg += admin_cmds
    await upd.message.reply_text(msg, parse_mode="HTML")

async def cmd_help(upd, ctx):
    await cmd_start(upd, ctx)

async def cache_user(upd, ctx):
    u = upd.effective_user
    if not u or not u.username: return
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO username_cache (username, chat_id, user_id, first_name) VALUES (%s,%s,%s,%s) ON CONFLICT (username, chat_id) DO UPDATE SET user_id=%s, first_name=%s",
                (u.username.lower(), upd.effective_chat.id, u.id, u.first_name or "", u.id, u.first_name or ""))
    conn.commit(); cur.close(); conn.close()

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
    app.add_handler(MessageHandler(filters.ALL, cache_user), group=0)
    for cmd, fn in [("start", cmd_start), ("help", cmd_help), ("casserole", cmd_casserole), ("profile", cmd_profile), ("me", cmd_profile),
                     ("balance", bal_command),
                     ("top", cmd_top), ("top_syrniki", cmd_top_syr), ("gift", cmd_gift),
                     ("salary", cmd_salary), ("givecoins", cmd_givecoins), ("buy", cmd_buy),
                     ("buy_s", cmd_buy_s), ("sell", cmd_sell), ("sell_s", cmd_sell_s),
                     ("coinflip", cmd_coinflip),
                     ("stealing", stealing), ("stealing_s", stealing_s), ("stealing_coins", stealing_coins),
                     ("transfer", cmd_transfer), ("transfer_s", cmd_transfer_s), ("transfer_coins", cmd_transfer_coins),
                     ("addrole", cmd_addrole), ("delrole", cmd_delrole),
                     ("addadmin", cmd_addadmin), ("deladmin", cmd_deladmin),
                     ("listadmins", cmd_listadmins), ("listroles", cmd_listadmins),
                     ("id", cmd_myid), ("myid", cmd_myid), ("get_sticker_id", cmd_stickerid), ("stickerid", cmd_stickerid),
                     ("msg_info", cmd_info), ("info", cmd_info)]:
        app.add_handler(CommandHandler(cmd, fn), group=1)
    app.add_handler(CallbackQueryHandler(coinflip_cb, pattern="^cf"), group=1)
    app.add_handler(CallbackQueryHandler(trade_cb, pattern="^[bs]_"), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.REPLY & filters.Regex(r'(?i)^casserole$'), text_casserole), group=1)
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, text_gift), group=1)
    print("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
