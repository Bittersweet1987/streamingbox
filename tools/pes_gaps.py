"""Zeitstempel-Lücken in einer MPEG-TS-Datei (z. B. dem Mitschnitt des Empfängers oder von srt-live-transmit): Aufruf pes_gaps.py datei.ts.
Zeigt je Strom (0xe0 Bild, 0xbd Ton) Anzahl, Median-Abstand, Lücken über dem Dreifachen und rückwärts laufende Zeit."""
import sys
d=open(sys.argv[1],"rb").read()
pts={}
for i in range(0,len(d)-188,188):
    if d[i]!=0x47: continue
    if not (d[i+1]&0x40): continue
    pid=((d[i+1]&0x1f)<<8)|d[i+2]; afc=(d[i+3]>>4)&3
    off=4
    if afc in (2,3): off+=1+d[i+4]
    if afc in (1,3) and d[i+off:i+off+3]==b"\x00\x00\x01":
        sid=d[i+off+3]; fl=d[i+off+7]
        if fl&0x80:
            b=d[i+off+9:i+off+14]
            p=((b[0]>>1)&7)<<30|(b[1]<<22)|((b[2]>>1)<<15)|(b[3]<<7)|(b[4]>>1)
            pts.setdefault(sid,[]).append(p/90000.0)
for sid,v in pts.items():
    dd=[v[j]-v[j-1] for j in range(1,len(v))]
    med=sorted(dd)[len(dd)//2]
    big=[(round(v[j]-v[0],1),round(dd[j-1],2)) for j in range(1,len(v)) if dd[j-1]>3*med or dd[j-1]<0]
    print("stream 0x%x: %d PES, %.1f s, Median %.4f, Lücken>3x: %d, größte %.2f, Rückwärts %d"%(sid,len(v),v[-1]-v[0],med,len(big),max(dd),sum(1 for x in dd if x<0)), big[:14])
