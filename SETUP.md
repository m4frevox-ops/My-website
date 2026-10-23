# تشغيل Night Call (البوت + الموقع) مجاناً 24/7

## 1) إعداد Discord Developer Portal
1. افتح https://discord.com/developers/applications ← تطبيقك (Client ID: `1562767651179204618`).
2. **Bot** ← فعّل **Server Members Intent** و**Message Content Intent** ← اضغط **Reset Token** وانسخه (للملف `.env` فقط).
3. **OAuth2** ← انسخ **Client Secret** (للملف `.env` فقط).
4. **OAuth2 ← Redirects** ← أضف: `https://اسم-دومينك.duckdns.org/auth/callback` (بعد الخطوة 3).
5. أضف البوت للسرفر بهذا الرابط:
   https://discord.com/oauth2/authorize?client_id=1562767651179204618&scope=bot&permissions=268553216
6. تأكد أن البوت يرى قناة الطلبات ويستطيع الكتابة فيها (View Channel + Send Messages + Embed Links).

## 2) دومين مجاني
1. ادخل https://www.duckdns.org وسجّل دخول، وأنشئ اسماً مثل `nightcall` ← دومينك `nightcall.duckdns.org`.
2. ضع عنوان IP الخادم (الخطوة 3) في خانة current ip.

## 3) خادم Oracle Cloud Always Free
1. أنشئ حساباً في https://www.oracle.com/cloud/free ثم **Create instance**: صورة **Ubuntu 22.04**، وshape من فئة **Always Free**، وحمّل مفتاح SSH.
2. في **VCN ← Security List** أضف Ingress للمنفذين **80** و **443** (TCP، المصدر `0.0.0.0/0`).
3. ادخل بـ SSH ونفّذ:
```bash
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo apt update && sudo apt install -y python3-venv python3-pip netfilter-persistent
sudo netfilter-persistent save
```

## 4) رفع الملفات
انقل مجلد المشروع (هذا المجلد) إلى `/home/ubuntu/night-call` (باستخدام scp أو WinSCP)، ثم:
```bash
cd ~/night-call
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env      # املأ القيم (التوكن والسر هنا فقط)
```
ولتوليد `SESSION_SECRET`: `python3 -c "import secrets;print(secrets.token_hex(32))"`

## 5) التشغيل الدائم (systemd)
```bash
sudo tee /etc/systemd/system/nightcall.service >/dev/null <<'EOF'
[Unit]
Description=Night Call
After=network-online.target
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/night-call
ExecStart=/home/ubuntu/night-call/venv/bin/python bot_server.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now nightcall
sudo journalctl -u nightcall -f        # لرؤية السجلات
```

## 6) HTTPS تلقائي (Caddy)
```bash
sudo apt install -y caddy
echo 'nightcall.duckdns.org { reverse_proxy 127.0.0.1:8080 }' | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy
```
ثم افتح `https://nightcall.duckdns.org`.

## ملاحظات
- وقت الفويس وعدد الرسائل والبوستات يبدأ العدّ من لحظة تشغيل البوت (لا يمكن جلب الماضي).
- وقت الفويس يُحسب كل دقيقة، ولا يُحسب في قناة AFK أو القنوات المذكورة في `EXCLUDED_VOICE_IDS`.
- الألعاب الأونلاين (البحث عن لاعب والغرف) ما زالت محاكاة، وتحتاج مرحلة WebSocket منفصلة.
- ملف `data.db` يحوي البيانات، فخذ منه نسخة احتياطية من وقت لآخر.

## نظام الليفلات (أوامر البوت، البادئة فاصلة منقوطة ;)
- `;rank [@شخص]` كارد الليفل | `;leader` لوائح التوب (Total وVoice وText)
- للأدمن فقط: `;addxp` `;removexp` `;addlevel` `;removelevel` بالصيغة: `;addxp @شخص text 500` (text أو voice)
- يحتاج البوت صلاحية **Manage Roles** ويجب أن تكون رتبته **أعلى** من رتب الجوائز.
- رسالة النص تعطي 15 إلى 25 XP كل دقيقة كحد أقصى، والفويس 10 XP كل دقيقة. يُعطى المستخدم أعلى رتبة وصل إليها فقط في كل فئة.
