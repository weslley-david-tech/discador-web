import os
from twilio.rest import Client
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import pandas as pd, re, uuid
from pathlib import Path

app = FastAPI(title="Discador PT-BR")
app.mount("/static", StaticFiles(directory="static"), name="static")
campaigns = {}
Path("uploads").mkdir(exist_ok=True)
Path("exports").mkdir(exist_ok=True)

def phone(value):
    n = re.sub(r"\D", "", str(value))

    if n.startswith("55"):
        n = n[2:]

    if len(n) not in (10, 11):
        return None

    return "+55" + n

@app.get("/", response_class=HTMLResponse)
def home():
    return Path("static/index.html").read_text(encoding="utf-8")

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsx",".xls")):
        raise HTTPException(400, "Envie um arquivo Excel.")
    cid = str(uuid.uuid4())[:8]
    path = Path("uploads") / f"{cid}.xlsx"
    path.write_bytes(await file.read())
    df = pd.read_excel(path, header=None, dtype=object)
    if df.shape[1] < 2:
        raise HTTPException(400, "A planilha precisa ter pelo menos 2 colunas.")
    records=[]; invalid=0
    for _, row in df.iloc[:, :2].iterrows():
        if pd.isna(row.iloc[0]) or pd.isna(row.iloc[1]): continue
        p=phone(row.iloc[0])
        if p:
            records.append({"telefone":p,"mensagem":str(row.iloc[1]).strip(),"status":"PENDENTE"})
        else: invalid += 1
    if len(records)>10000: records=records[:10000]
    campaigns[cid]={"records":records,"index":0,"state":"PRONTA","invalid":invalid}
    return {"id":cid,"validos":len(records),"invalidos":invalid,"total":len(records)}

@app.get("/api/campaign/{cid}")
def status(cid:str):
    c=campaigns.get(cid)
    if not c: raise HTTPException(404,"Campanha não encontrada")
    done=sum(r["status"]!="PENDENTE" for r in c["records"])
    counts={}
    for r in c["records"]: counts[r["status"]]=counts.get(r["status"],0)+1
    return {"state":c["state"],"total":len(c["records"]),"processed":done,"counts":counts,
            "current": c["records"][c["index"]] if c["index"]<len(c["records"]) else None}

@app.post("/api/campaign/{cid}/start")
def start(cid: str):
    c = campaigns.get(cid)

    if not c:
        raise HTTPException(404, "Campanha não encontrada")

    c["state"] = "RODANDO"

    if c["index"] >= len(c["records"]):
        return {"ok": True, "message": "Campanha concluída"}

    r = c["records"][c["index"]]

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")

    if not account_sid or not auth_token or not twilio_number:
        raise HTTPException(
            500,
            "Credenciais da Twilio não configuradas"
        )

    client = Client(account_sid, auth_token)

    try:

    call = client.calls.create(
        to=r["telefone"],
        from_=twilio_number,
        twiml=f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="pt-BR">
        {r["mensagem"]}
    </Say>
</Response>"""
    )

    r["status"] = "EM CHAMADA"
    r["call_sid"] = call.sid
        )

        r["status"] = "EM CHAMADA"
        r["call_sid"] = call.sid

        return {
            "ok": True,
            "sid": call.sid
        }

    except Exception as e:
        r["status"] = "FALHOU"
        raise HTTPException(
            500,
            f"Erro ao realizar ligação: {str(e)}"
        )
    c=campaigns.get(cid)
    if not c: raise HTTPException(404,"Campanha não encontrada")
    c["state"]="RODANDO"
    # MVP: avança manualmente para simular processamento. Telefonia real entra em adaptador autorizado.
    if c["index"] < len(c["records"]):
        r=c["records"][c["index"]]
        r["status"]="PRONTO PARA CHAMADA"
    return {"ok":True}

@app.post("/api/campaign/{cid}/pause")
def pause(cid:str):
    if cid not in campaigns: raise HTTPException(404,"Campanha não encontrada")
    campaigns[cid]["state"]="PAUSADA"
    return {"ok":True}

@app.post("/api/campaign/{cid}/stop")
def stop(cid:str):
    if cid not in campaigns: raise HTTPException(404,"Campanha não encontrada")
    campaigns[cid]["state"]="ENCERRADA"
    return {"ok":True}

@app.post("/api/campaign/{cid}/result/{result}")
def result(cid:str,result:str):
    c=campaigns.get(cid)
    if not c: raise HTTPException(404,"Campanha não encontrada")
    allowed={"ATENDEU","NAO_ATENDEU","FALHOU"}
    if result not in allowed: raise HTTPException(400,"Resultado inválido")
    if c["index"]>=len(c["records"]): raise HTTPException(400,"Campanha concluída")
    c["records"][c["index"]]["status"]=result
    c["index"]+=1
    if c["state"]=="RODANDO" and c["index"]<len(c["records"]):
        c["records"][c["index"]]["status"]="PRONTO PARA CHAMADA"
    return {"ok":True}

@app.get("/api/campaign/{cid}/export")
def export(cid:str):
    c=campaigns.get(cid)
    if not c: raise HTTPException(404,"Campanha não encontrada")
    out=Path("exports")/f"resultado_{cid}.xlsx"
    pd.DataFrame(c["records"]).to_excel(out,index=False)
    return FileResponse(out, filename=out.name)
