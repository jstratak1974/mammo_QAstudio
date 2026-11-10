"""Offline QA report writers and shared plot styling."""
from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib import get_data_path

from qa_metrics import VERSION

INK = "#172E45"
ACCENT = "#087D82"
MUTED = "#52687C"
LIGHT = "#EAF3F5"
COLORS = [ACCENT, "#3468A5", "#B25E31", "#7F529D", "#647A32"]


def native(value):
    if isinstance(value,np.generic):
        return native(value.item())
    if isinstance(value,float) and not math.isfinite(value):
        return None
    if isinstance(value,np.ndarray):
        return native(value.tolist())
    if isinstance(value,(list,tuple)):
        return [native(x) for x in value]
    if isinstance(value,dict):
        return {str(k):native(v) for k,v in value.items()}
    return value


def display(value):
    value=native(value)
    if value is None:
        return ""
    if isinstance(value,float):
        return f"{value:.6g}"
    if isinstance(value,(dict,list)):
        return json.dumps(value,ensure_ascii=False)
    return str(value)


def spreadsheet_value(value):
    value=native(value)
    if isinstance(value,(list,dict)):
        value=display(value)
    # Keep literal text in CSV/legacy Excel even if an entry resembles a formula.
    if isinstance(value,str) and (value.lstrip().startswith(("=","+","-","@")) or value.startswith(("\t","\r"))):
        return "'"+value
    return value


def draw_chart(ax,chart):
    ax.clear()
    ax.set_title(chart.title,loc="left",fontsize=11,fontweight="bold",color=INK,pad=9)
    ax.set(xlabel=chart.xlabel,ylabel=chart.ylabel)
    if chart.log:
        ax.set_yscale("log")
    else:
        ax.set_yscale("linear")
    for i,(name,x,y) in enumerate(chart.series):
        x=np.asarray(x,float);y=np.asarray(y,float)
        if chart.log:
            y=np.where(y>0,y,np.nan)
        ax.plot(x,y,label=name,color=COLORS[i%len(COLORS)],linewidth=1.45,
                marker=chart.marker or None,markersize=3.2)
    ax.grid(alpha=.24,linewidth=.6)
    for spine in ("top","right"):
        ax.spines[spine].set_visible(False)
    if len(chart.series)>1:
        ax.legend(frameon=False,fontsize=8,loc="best")
    ax.tick_params(labelsize=8)
    ax.xaxis.label.set_size(9);ax.yaxis.label.set_size(9)


def chart_png(chart, width=6.75, height=2.25):
    fig=Figure(figsize=(width,height),dpi=150,layout="tight",facecolor="white")
    canvas=FigureCanvasAgg(fig)
    draw_chart(fig.add_subplot(111),chart)
    stream=BytesIO()
    canvas.print_png(stream);stream.seek(0)
    return stream


def record_sections(meta, records):
    """Yield sheets in display order, with real numeric cells."""
    yield "Session",("Field","Value"),[(k,v) for k,v in meta.items()]+[("Software",f"Mammo QA Studio {VERSION}")]
    headers=("ID","Test","Run","Date/time","Metric","Value","Unit","User criterion","Status")
    yield "Summary",headers,[(r.id,r.test,r.label,r.timestamp,m.label,m.value,m.unit,m.criterion,m.status)
                            for r in records for m in r.metrics]
    yield "Methods and inputs",("ID","Test","Item","Value"),[
        row for r in records for row in (
            [(r.id,r.test,k,v) for k,v in r.inputs.items()]+[(r.id,r.test,"Note",n) for n in r.notes])]
    for r in records:
        for t in r.tables:
            yield f"{r.id:02d} {t.title}",t.columns,t.rows


def write_csv(path,meta,records):
    with open(path,"w",encoding="utf-8-sig",newline="") as stream:
        w=csv.writer(stream)
        w.writerow(("Section","Row","Field","Value"))
        for title,cols,rows in record_sections(meta,records):
            for i,row in enumerate(rows,1):
                for field,value in zip(cols,row):
                    w.writerow([spreadsheet_value(v) for v in (title,i,field,value)])


def write_table_csv(path,table):
    with open(path,"w",encoding="utf-8-sig",newline="") as stream:
        w=csv.writer(stream);w.writerow([spreadsheet_value(v) for v in table.columns])
        w.writerows([spreadsheet_value(v) for v in row] for row in table.rows)


def write_json(path,meta,records):
    def record(r):
        # The 2D spectra are available separately as NPZ.
        return {k:native(v if k not in ("metrics","tables","charts") else [asdict(x) for x in v])
                for k,v in vars(r).items() if k!="arrays"}
    Path(path).write_text(json.dumps({"app":"Mammo QA Studio","version":VERSION,
                        "session":native(meta),"results":[record(r) for r in records]},
                        ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


def sheet_name(name, used):
    base=re.sub(r"[\\/*?:\[\]]"," ",name).strip("' ") or "Data"
    candidate=base[:31]
    idx=2
    while candidate.casefold() in used:
        tail=f" {idx}";candidate=base[:31-len(tail)]+tail;idx+=1
    used.add(candidate.casefold())
    return candidate


def widths(headers, rows):
    return [min(59,max(11,len(str(h))+2,max((len(display(r[i]))+2 for r in rows[:75]),default=0)))
            for i,h in enumerate(headers)]


def write_xlsx(path,meta,records):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.chart import ScatterChart, Reference, Series
    wb=Workbook();wb.remove(wb.active)
    used=set()
    dark=PatternFill("solid",fgColor=INK[1:]);pale=PatternFill("solid",fgColor="EEF5F7")
    for title,headers,rows in record_sections(meta,records):
        ws=wb.create_sheet(sheet_name(title,used));ws.freeze_panes="A2";ws.sheet_view.showGridLines=False
        ws.append(headers)
        for cell in ws[1]:
            cell.fill=dark;cell.font=Font(name="Calibri",bold=True,color="FFFFFF")
            cell.alignment=Alignment(vertical="center",wrap_text=True)
        ws.row_dimensions[1].height=29
        col_width=widths(headers,rows)
        for i,w in enumerate(col_width,1):
            ws.column_dimensions[get_column_letter(i)].width=w
        for row_index,row in enumerate(rows,2):
            ws.append([spreadsheet_value(v) for v in row])
            lines=1
            for col_index,cell in enumerate(ws[row_index]):
                if row_index%2==0:
                    cell.fill=pale
                if isinstance(cell.value,float):
                    cell.number_format="0.00000E+00" if abs(cell.value)<1e-3 and cell.value else "0.######"
                if isinstance(cell.value,str):
                    if cell.value in ("PASS","FAIL"):
                        cell.font=Font(bold=True,color="156551" if cell.value=="PASS" else "A12C38")
                    if len(cell.value)>col_width[col_index]-2:
                        cell.alignment=Alignment(wrap_text=True,vertical="top")
                        lines=max(lines,math.ceil(len(cell.value)/(col_width[col_index]-3)))
            if lines>1:
                ws.row_dimensions[row_index].height=min(165,16*lines+3)
        if rows:
            ws.auto_filter.ref=ws.dimensions
        if rows and str(headers[0]).startswith("Frequency") and len(headers)>1:
            ch=ScatterChart()
            ch.title=title.split(" ",1)[-1];ch.x_axis.title=str(headers[0]);ch.y_axis.title="Measured values"
            ch.width=16;ch.height=8.5
            xr=Reference(ws,min_col=1,min_row=2,max_row=ws.max_row)
            # DQE columns have different units; chart DQE percentage only.
            indices=[3] if "DQE curve" in title else range(2,len(headers)+1)
            for col in indices:
                ser=Series(Reference(ws,min_col=col,min_row=2,max_row=ws.max_row),xr,title=str(headers[col-1]))
                ser.marker.symbol="none";ser.graphicalProperties.line.width=20000
                ch.series.append(ser)
            ws.add_chart(ch,f"{get_column_letter(len(headers)+2)}2")
    wb.active=1 if len(wb.sheetnames)>1 else 0
    wb.save(path)


def write_xls(path,meta,records):
    import xlwt
    wb=xlwt.Workbook(encoding="utf-8");used=set()
    head=xlwt.easyxf("font: bold on, colour white; pattern: pattern solid, fore_colour dark_blue; alignment: wrap on")
    num=xlwt.easyxf(num_format_str="0.######")
    sci=xlwt.easyxf(num_format_str="0.00000E+00")
    wrapped=xlwt.easyxf("alignment: wrap on, vert top")
    for title,headers,rows in record_sections(meta,records):
        for start in range(0,max(1,len(rows)),65535):
            tab=wb.add_sheet(sheet_name(title+(" continued" if start else ""),used))
            tab.set_panes_frozen(True);tab.set_horz_split_pos(1)
            ws=widths(headers,rows)
            for j,h in enumerate(headers):
                tab.write(0,j,str(h),head);tab.col(j).width=256*ws[j]
            for i,row in enumerate(rows[start:start+65535],1):
                for j,value in enumerate(row):
                    v=spreadsheet_value(value)
                    if v is None:
                        continue
                    if isinstance(v,float):
                        tab.write(i,j,v,sci if 0<abs(v)<1e-3 else num)
                    elif isinstance(v,str) and len(v)>ws[j]-2:
                        tab.write(i,j,v[:32767],wrapped)
                        tab.row(i).height=max(tab.row(i).height,min(3300,300*math.ceil(len(v)/(ws[j]-3))))
                    else:
                        tab.write(i,j,v)
    wb.save(str(path))


def write_pdf(path,meta,records, include_charts=True):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, Image, KeepTogether

    fontdir=Path(get_data_path())/"fonts"/"ttf"
    pdfmetrics.registerFont(TTFont("QARegular",str(fontdir/"DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("QABold",str(fontdir/"DejaVuSans-Bold.ttf")))
    ss={
        "title":ParagraphStyle("title",fontName="QABold",fontSize=24,leading=29,textColor=colors.HexColor(INK),spaceAfter=5),
        "subtitle":ParagraphStyle("subtitle",fontName="QARegular",fontSize=9,leading=13,textColor=colors.HexColor(MUTED),spaceAfter=12),
        "head":ParagraphStyle("head",fontName="QABold",fontSize=13,leading=18,textColor=colors.HexColor(INK),
                              spaceBefore=14,spaceAfter=5,keepWithNext=True),
        "subhead":ParagraphStyle("subhead",fontName="QABold",fontSize=9,leading=13,textColor=colors.HexColor(ACCENT),
                                 spaceBefore=6,spaceAfter=3,keepWithNext=True),
        "body":ParagraphStyle("body",fontName="QARegular",fontSize=8.2,leading=12,textColor=colors.HexColor(INK),spaceAfter=4),
        "cell":ParagraphStyle("cell",fontName="QARegular",fontSize=7.6,leading=10.5,textColor=colors.HexColor(INK)),
        "th":ParagraphStyle("th",fontName="QABold",fontSize=7.5,leading=10.2,textColor=colors.white),
    }
    def p(text,key="body"):
        return Paragraph(escape(display(text)).replace("\n","<br/>"),ss[key])
    def table(cols,rows,width):
        cells=[[p(c,"th") for c in cols]]+[[p(v,"cell") for v in row] for row in rows]
        t=LongTable(cells,colWidths=width,repeatRows=1,hAlign="LEFT")
        t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor(INK)),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor(LIGHT)]),
            ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),6),
            ("RIGHTPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),4),
            ("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("LINEBELOW",(0,-1),(-1,-1),.45,colors.HexColor("#B5CED6"))]))
        return t
    doc=SimpleDocTemplate(str(path),pagesize=A4,leftMargin=42,rightMargin=42,topMargin=41,bottomMargin=44,
                          title="Mammography quality assurance",author="Mammo QA Studio")
    width=doc.width
    story=[p("Mammography QA","title"),
           p(f"MEASUREMENT REPORT  /  {len(records)} result sets  /  Mammo QA Studio {VERSION}","subtitle")]
    fields=[(k,v) for k,v in meta.items() if v]
    if fields:
        story.append(table(("Session field","Entry"),fields,(width*.25,width*.75)))
    story.append(Spacer(1,5))
    story.append(p("Status is based on entered criteria. Blank criteria indicate a recorded measurement without a pass/fail assessment."))
    for r in records:
        story.append(p(f"{r.id:02d}  {r.test}"+(f" / {r.label}" if r.label else ""),"head"))
        rows=[(m.label,m.value,m.unit,m.criterion,m.status) for m in r.metrics]
        story.append(table(("Metric","Value","Unit","User criterion","Status"),rows,
                           [width*f for f in (.39,.16,.12,.20,.13)]))
        if r.inputs:
            story.append(p("Inputs and method","subhead"))
            story.append(table(("Input","Value"),list(r.inputs.items()),[width*.38,width*.62]))
        for n in r.notes:
            story.append(p(n))
        for t in r.tables:
            if len(t.rows)<=16 and len(t.columns)<=8 and t.rows and "curve" not in t.title.lower():
                story.append(p(t.title,"subhead"))
                weights=[max(1,min(1.85,len(str(h))/11)) for h in t.columns]
                story.append(table(t.columns,t.rows,[width*x/sum(weights) for x in weights]))
        if include_charts and r.charts:
            stream=chart_png(r.charts[0])
            story.append(KeepTogether([Spacer(1,7),Image(stream,width=width,height=width/3)]))
    story.append(Spacer(1,12))
    story.append(p("The Excel, CSV and JSON exports contain detailed numeric tables and complete sampled curves."))
    def footer(c, d):
        w,_=A4;c.setStrokeColor(colors.HexColor("#B5CED6"));c.line(42,34,w-42,34)
        c.setFont("QARegular",8);c.setFillColor(colors.HexColor(MUTED))
        c.drawString(42,22,"Mammo QA Studio  |  Detector QA")
        c.drawRightString(w-42,22,f"Page {d.page}")
    doc.build(story,onFirstPage=footer,onLaterPages=footer)


WRITERS={".pdf":write_pdf,".xlsx":write_xlsx,".xls":write_xls,".csv":write_csv,".json":write_json}
