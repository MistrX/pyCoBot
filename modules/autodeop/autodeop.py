# -*- coding: utf-8 -*-
from pycobot.pycobot import BaseModel
from peewee.peewee import CharField


class autodeop:

    def __init__(self, core, client):
        core.addCommandHandler("autodeop", self, cpriv=6, cprivchan=True, chelp=
        "Activa o desactiva el autodeop en un canal. Sintaxis: autodeop <canal>"
        " <on/off>")
        try:
            autodeopt.create_table()
        except:
            pass
        core.addHandler("mode", self, "modeprot")

    def autodeop_p(self, bot, cli, event):
        if len(event.splitd) > 0:
            return event.splitd[0]
        return 1

    def autodeop(self, bot, cli, ev):
        if len(ev.splitd) < 1:
            cli.privmsg(ev.target, "\00304Error\003: Faltan parametros.")
            return 1

        ch = autodeopt.select().where(autodeopt.channel == ev.splitd[0])

        if ev.splitd[1] == "on":
            if ch.count() == 0:
                autodeopt.create(channel=ev.splitd[0])
                cli.privmsg(ev.target, "Se ha activado el autodeop en \2" +
                 ev.splitd[0])
            else:
                cli.privmsg(ev.target, "\00304Error\003: El autodeop ya esta a"
                "ctivado en el canal \2" + ev.splitd[0])
        else:
            if ch.count() != 0:
                r = autodeopt.get(autodeopt.channel == ev.splitd[0])
                r.delete_instance()
                cli.privmsg(ev.target, "Se ha desactivado el autodeop en \2" +
                 ev.splitd[0])
            else:
                cli.privmsg(ev.target, "\00304Error\003: El autodeop no esta a"
                "ctivado en el canal \2" + ev.splitd[0])

    def modeprot(self, cli, ev):
        x = self.parsemode(cli, ev)
        for w in x:
            cli.mode(ev.target, "-o " + w)

    def parsemode(self, cli, ev):
        res = []
        cmodelist = cli.features.chanmodes
        param = cmodelist[0] + cmodelist[1] + cmodelist[2]
        for i, val in enumerate(cli.features.prefix):
            param = param + cli.features.prefix[val]
        pos = 0
        for c in ev.arguments[0]:
            if c == "-":
                rving = False
                pass
            elif c == "+":
                rving = True
            else:
                if c in param:
                    pos = pos + 1
            if rving is False:
                continue

            if c == "o":
                res.append(ev.arguments[pos])  # BEEP BEEP BEEP BEEP
        return res


class autodeopt(BaseModel):
    channel = CharField(primary_key=True)

    class Meta:
        db_table = "autodeop"