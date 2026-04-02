from telethon.sync import TelegramClient
c = TelegramClient('telegram_session', 30498097, 'd2bd558312d0fa6faac833bad2d34cce')
c.start(phone='+610449897659')
print('Done! Session created.')
c.disconnect()
