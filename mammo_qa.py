#!/usr/bin/env python3
"""Mammo QA Studio: single-window mammography QA desktop application."""
from __future__ import annotations

import argparse
import math
import queue
import threading
import tkinter as tk
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from tkinter import ttk, messagebox, filedialog, simpledialog, font
from tkinter.scrolledtext import ScrolledText

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.widgets import RectangleSelector
from matplotlib.patches import Rectangle

import coq_core as coq
import qa_metrics as qa
import qa_reports as reports

BACKGROUND="#F0F5F8"
WHITE="#FFFFFF"
ROLES={"Analysis":"#21DDCC","Signal":"#FFBF65","Background":"#71CBFF",
       "R1":"#F8DC69","R2":"#F5A4FF","R3":"#8FFC83"}
CATEGORIES={"Beam":("kVp","HVL","Tube output","Mean glandular dose"),
            "Image QA":("SNR","CNR / SDNR","Ghost factor","Uniformity","Defective pixels"),
            "Transfer":("MTF","NPS / NNPS","DQE","Response calibration")}


@dataclass
class Loaded:
    image: coq.ImageRecord
    label: str
    rois: dict = field(default_factory=dict)
    override: tuple | None = None

    @property
    def spacing(self):
        return self.override if self.override is not None else self.image.spacing


class ScrollForm(ttk.Frame):
    def __init__(self,parent):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,background=WHITE,highlightthickness=0)
        scroll=ttk.Scrollbar(self,orient="vertical",command=self.canvas.yview)
        scroll.pack(side="right",fill="y");self.canvas.pack(side="left",fill="both",expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.body=ttk.Frame(self.canvas,padding=(11,8))
        item=self.canvas.create_window(0,0,anchor="nw",window=self.body)
        self.canvas.bind("<Configure>",lambda e:self.canvas.itemconfigure(item,width=e.width))
        self.body.bind("<Configure>",lambda _:self.canvas.configure(scrollregion=self.canvas.bbox("all")))


class MammoQA:
    def __init__(self,root):
        self.root=root
        self.root.title(f"Mammo QA Studio {qa.VERSION} | Mammography quality assurance")
        w=min(1450,max(1080,root.winfo_screenwidth()-60))
        h=min(940,max(710,root.winfo_screenheight()-95))
        self.root.geometry(f"{w}x{h}");self.root.minsize(1050,705)
        self.root.configure(background=BACKGROUND)
        self.images=[];self.active=None;self.image_serial=0
        self.results=[];self.result_serial=0;self.current=None
        self.response=coq.Response()
        self.events=queue.Queue();self.busy=False;self.closing=False
        self.vars={};self.texts={};self.measurements={};self.forms={}
        self.role=tk.StringVar(value="Analysis")
        self.run_label=tk.StringVar()
        self.frame=tk.StringVar(value="1")
        self.dicom_lut=tk.BooleanVar(value=False)
        self.signal_mode=tk.StringVar(value="Stored signal")
        self.window=None
        self.status=tk.StringVar(value="Load detector DICOM files or try the synthetic demonstration.")
        self.image_title=tk.StringVar(value="No image loaded")
        self.measurement_count=tk.StringVar(value="0 result sets")
        self.calibration_status=tk.StringVar(value="No calibration fitted")
        self.result_title=tk.StringVar(value="Results")
        self.plot_choice=tk.StringVar()
        self._style()
        self._build()
        self.root.after(75,self._poll)
        self.root.protocol("WM_DELETE_WINDOW",self._close)

    def _style(self):
        style=ttk.Style(self.root)
        if "clam" in style.theme_names():style.theme_use("clam")
        families=set(font.families(self.root))
        family=next((f for f in ("Segoe UI","DejaVu Sans","Arial","Liberation Sans") if f in families),
                    font.nametofont("TkDefaultFont",root=self.root).actual("family"))
        font.nametofont("TkDefaultFont",root=self.root).configure(family=family,size=10)
        style.configure(".",font=(family,10))
        style.configure("TFrame",background=WHITE)
        style.configure("TLabel",background=WHITE,foreground=reports.INK)
        style.configure("Hint.TLabel",foreground=reports.MUTED)
        style.configure("Title.TLabel",font=(family,19,"bold"),foreground=reports.INK)
        style.configure("Section.TLabel",font=(family,11,"bold"),foreground=reports.INK)
        style.configure("TButton",padding=(8,5))
        style.configure("Primary.TButton",background=reports.ACCENT,foreground=WHITE,padding=(10,6))
        style.map("Primary.TButton",background=[("active","#096568"),("disabled","#A5BABC")],
                  foreground=[("disabled",WHITE)])
        style.configure("TCheckbutton",background=WHITE)
        style.configure("TNotebook",background=BACKGROUND,borderwidth=0)
        style.configure("TNotebook.Tab",padding=(11,6))
        style.map("TNotebook.Tab",foreground=[("selected",reports.ACCENT)],background=[("selected",WHITE)])
        style.configure("Treeview",rowheight=24,fieldbackground=WHITE,foreground=reports.INK)
        style.configure("Treeview.Heading",background="#E1ECF0",foreground=reports.INK,font=(family,9,"bold"))

    def _button(self,p,text,command,*,primary=False):
        return ttk.Button(p,text=text,command=lambda:self._guard(command),
                          style="Primary.TButton" if primary else "TButton")

    def _guard(self,action):
        try:
            action()
        except Exception as exc:
            self.status.set(str(exc))
            messagebox.showerror("Mammo QA Studio",str(exc),parent=self.root)

    def _hint(self,p,text):
        l=ttk.Label(p,text=text,style="Hint.TLabel",wraplength=350,justify="left")
        l.pack(fill="x",pady=(4,7))
        return l

    def _var(self,key,default=""):
        if key not in self.vars:self.vars[key]=tk.StringVar(value=str(default))
        return self.vars[key]

    def _field(self,p,label,key,default="",choices=None):
        row=ttk.Frame(p);row.pack(fill="x",pady=2)
        ttk.Label(row,text=label,width=26).pack(side="left",anchor="w")
        var=self._var(key,default)
        if choices is None:
            w=ttk.Entry(row,textvariable=var,width=18)
        else:
            w=ttk.Combobox(row,textvariable=var,values=choices,state="readonly",width=17)
        w.pack(side="left",fill="x",expand=True)
        return w

    def _v(self,key):
        return self.vars[key].get().strip()

    def _n(self,key,*,optional=False,minimum=None):
        return qa.number(self._v(key),key.replace("_"," "),optional=optional,minimum=minimum)

    def _integer(self,key):
        n=self._n(key)
        if not n.is_integer():raise ValueError(f"{key.replace('_',' ')} must be an integer.")
        return int(n)

    def _text_input(self,p,key):
        w=ScrolledText(p,height=6,wrap="none",font=("TkFixedFont",10),borderwidth=1,relief="solid",
                       background="#F7FBFC",foreground=reports.INK)
        w.pack(fill="x",pady=(0,6))
        w.insert("1.0",self.measurements.get(key,""))
        self.texts[key]=w
        return w

    def _readings(self,key,columns):
        return qa.pairs(self.texts[key].get("1.0","end-1c"),columns)

    def _build(self):
        self.root.columnconfigure(0,weight=1);self.root.rowconfigure(1,weight=1)
        header=ttk.Frame(self.root,padding=(14,9));header.grid(row=0,column=0,sticky="ew")
        ttk.Label(header,text="Mammo QA Studio",style="Title.TLabel").pack(side="left")
        ttk.Label(header,text="  /  Mammography quality assurance",style="Hint.TLabel").pack(side="left")
        self._button(header,"Help",self.help).pack(side="right",padx=(5,0))
        self._button(header,"Export report",self.export_report,primary=True).pack(side="right",padx=(5,0))
        self._button(header,"Load demo",self.demo).pack(side="right",padx=(5,0))
        self._button(header,"Open DICOM",self.open_dicom,primary=True).pack(side="right",padx=(5,0))
        ttk.Label(self.root,textvariable=self.status,padding=(13,5),style="Hint.TLabel",
                  wraplength=max(1000,min(1360,self.root.winfo_screenwidth()-60))).grid(row=2,column=0,sticky="ew")
        panes=ttk.Panedwindow(self.root,orient="horizontal")
        panes.grid(row=1,column=0,sticky="nsew",padx=9,pady=(6,4))
        left=ttk.Frame(panes,width=440);right=ttk.Frame(panes)
        panes.add(left,weight=0);panes.add(right,weight=1)
        self._build_left(left)
        right_pane=ttk.Panedwindow(right,orient="vertical")
        right_pane.pack(fill="both",expand=True)
        viewer=ttk.Frame(right_pane);results=ttk.Frame(right_pane)
        right_pane.add(viewer,weight=3);right_pane.add(results,weight=2)
        self._build_viewer(viewer);self._build_results(results)
        for canvas in (self.image_canvas,self.plot_canvas):
            canvas.get_tk_widget().bind("<Configure>",lambda _:self.root.after_idle(self._sync_canvases),add="+")
        self.root.after(175,self._sync_canvases)
        self._render_image()

    def _build_left(self,p):
        bank=ttk.Frame(p,padding=(11,8));bank.pack(fill="x")
        ttk.Label(bank,text="Acquisitions",style="Section.TLabel").pack(anchor="w")
        ttk.Label(bank,text="Ctrl-click to select multiple matched flat fields.",style="Hint.TLabel").pack(anchor="w")
        row=ttk.Frame(bank);row.pack(fill="x",pady=(5,3))
        self.image_list=tk.Listbox(row,height=4,selectmode="extended",exportselection=False,
                    background="#F7FBFC",foreground=reports.INK,selectbackground=reports.ACCENT,relief="flat")
        scroll=ttk.Scrollbar(row,command=self.image_list.yview);scroll.pack(side="right",fill="y")
        self.image_list.config(yscrollcommand=scroll.set);self.image_list.pack(side="left",fill="x",expand=True)
        self.image_list.bind("<<ListboxSelect>>",self.choose_image)
        buttons=ttk.Frame(bank);buttons.pack(fill="x")
        self._button(buttons,"Remove",self.remove_image).pack(side="left")
        self._button(buttons,"Image info",self.image_info).pack(side="left",padx=3)
        self._button(buttons,"Pixel pitch",self.pixel_pitch).pack(side="left")
        ttk.Label(buttons,text="Frame").pack(side="left",padx=(7,3))
        ttk.Entry(buttons,textvariable=self.frame,width=5).pack(side="left")
        ttk.Checkbutton(bank,text="Apply DICOM modality transform when opening",variable=self.dicom_lut).pack(anchor="w",pady=(4,0))
        row=ttk.Frame(bank);row.pack(fill="x",pady=(5,0))
        ttk.Label(row,text="Run label").pack(side="left",padx=(0,6))
        ttk.Entry(row,textvariable=self.run_label).pack(side="left",fill="x",expand=True)

        self.tabs=ttk.Notebook(p);self.tabs.pack(fill="both",expand=True)
        for cat,options in CATEGORIES.items():
            pane=ttk.Frame(self.tabs);self.tabs.add(pane,text=cat)
            control=ttk.Frame(pane,padding=(9,6));control.pack(fill="x")
            select=ttk.Combobox(control,values=options,textvariable=self._var("test_"+cat,options[0]),
                               state="readonly",width=23)
            select.pack(side="left",fill="x",expand=True)
            select.bind("<<ComboboxSelected>>",lambda _,c=cat:self._render_form(c))
            self._button(control,"Calculate",lambda c=cat:self.calculate(c),primary=True).pack(side="right",padx=(6,0))
            sf=ScrollForm(pane);sf.pack(fill="both",expand=True);self.forms[cat]=sf
            self._render_form(cat)
        report=ScrollForm(self.tabs);self.tabs.add(report,text="Session")
        self._hint(report.body,"Enter session identifiers and your protocol. Limits belong to each test and are exported alongside the result.")
        for label,key,default in (("Facility","facility",""),("System / serial","system",""),("Operator","operator",""),
                                   ("QA date","qa_date",date.today().isoformat()),("Protocol / revision","protocol",""),
                                   ("Meter / calibration ID","meter",""),("Session notes","session_notes","")):
            self._field(report.body,label,key,default)
        ttk.Label(report.body,textvariable=self.measurement_count,style="Section.TLabel").pack(anchor="w",pady=(13,4))
        self._button(report.body,"Export PDF / Excel / CSV / JSON",self.export_report,primary=True).pack(fill="x",pady=4)
        self._button(report.body,"Export current table CSV",self.export_current_table).pack(fill="x",pady=4)
        self._button(report.body,"Save selected spectrum NPZ",self.export_npz).pack(fill="x",pady=4)

    def _build_viewer(self,p):
        bar=ttk.Frame(p,padding=(9,5));bar.pack(fill="x")
        ttk.Label(bar,textvariable=self.image_title,style="Section.TLabel").pack(side="left",fill="x",expand=True)
        self._button(bar,"Window",self.set_window).pack(side="right",padx=3)
        self._button(bar,"Auto window",self.auto_window).pack(side="right")
        row=ttk.Frame(p,padding=(9,3));row.pack(fill="x")
        ttk.Label(row,text="ROI").pack(side="left",padx=(0,5))
        role=ttk.Combobox(row,values=tuple(ROLES),state="readonly",width=12,textvariable=self.role)
        role.pack(side="left",padx=(0,7));role.bind("<<ComboboxSelected>>",lambda _:self._roi_entries())
        self.roi_fields={}
        for key in ("X","Y","W","H"):
            ttk.Label(row,text=key).pack(side="left",padx=(0,2))
            v=tk.StringVar();self.roi_fields[key]=v
            ttk.Entry(row,textvariable=v,width=5).pack(side="left",padx=(0,4))
        self._button(row,"Set",self.set_roi).pack(side="left")
        self._button(row,"Full",self.full_roi).pack(side="left",padx=3)
        self._button(row,"Clear",self.clear_roi).pack(side="left")
        self.image_fig=Figure(figsize=(6.4,3),dpi=95,facecolor=WHITE,layout="tight")
        self.image_ax=self.image_fig.add_subplot(111)
        self.image_canvas=FigureCanvasTkAgg(self.image_fig,master=p)
        self.toolbar=NavigationToolbar2Tk(self.image_canvas,p,pack_toolbar=False)
        self.toolbar.pack(side="bottom",fill="x")
        self.image_canvas.get_tk_widget().pack(fill="both",expand=True)
        self.selector=None;self.roi_artists=[]

    def _build_results(self,p):
        bar=ttk.Frame(p,padding=(9,4));bar.pack(fill="x")
        ttk.Label(bar,textvariable=self.result_title,style="Section.TLabel").pack(side="left")
        self._button(bar,"Remove result",self.remove_result).pack(side="right")
        self._button(bar,"Table CSV",self.export_current_table).pack(side="right",padx=4)
        self._button(bar,"Plot PNG",self.export_plot).pack(side="right")
        self.results_tabs=ttk.Notebook(p);self.results_tabs.pack(fill="both",expand=True)
        overview=ttk.Frame(self.results_tabs);details=ttk.Frame(self.results_tabs);plot=ttk.Frame(self.results_tabs)
        self.results_tabs.add(overview,text="History")
        self.results_tabs.add(details,text="Values / methods")
        self.results_tabs.add(plot,text="Plot")
        cols=("id","test","label","metric","value","status")
        self.tree=ttk.Treeview(overview,columns=cols,show="headings",selectmode="browse",height=5)
        for col,title,w in zip(cols,("ID","Test","Run label","First metric","Value","Assessment"),(43,125,128,160,94,105)):
            self.tree.heading(col,text=title);self.tree.column(col,width=w,stretch=col in ("label","metric"),minwidth=32)
        scroll=ttk.Scrollbar(overview,command=self.tree.yview);scroll.pack(side="right",fill="y")
        self.tree.config(yscrollcommand=scroll.set);self.tree.pack(fill="both",expand=True)
        self.tree.tag_configure("PASS",foreground="#156551");self.tree.tag_configure("FAIL",foreground="#A12C38")
        self.tree.bind("<<TreeviewSelect>>",self.choose_result)
        self.detail=ScrolledText(details,height=8,wrap="word",font=("TkFixedFont",10),relief="flat",padx=11,pady=8)
        self.detail.pack(fill="both",expand=True);self.detail.configure(state="disabled")
        row=ttk.Frame(plot,padding=(8,3));row.pack(fill="x")
        ttk.Label(row,text="View").pack(side="left",padx=(0,6))
        self.plot_picker=ttk.Combobox(row,textvariable=self.plot_choice,values=(),state="readonly")
        self.plot_picker.pack(side="left",fill="x",expand=True)
        self.plot_picker.bind("<<ComboboxSelected>>",lambda _:self._draw_plot())
        self.plot_fig=Figure(figsize=(6.4,2.5),dpi=95,facecolor=WHITE,layout="tight")
        self.plot_canvas=FigureCanvasTkAgg(self.plot_fig,master=plot)
        self.plot_canvas.get_tk_widget().pack(fill="both",expand=True)
        self._draw_plot()

    def _sync_canvases(self):
        if self.closing:return
        for c in (self.image_canvas,self.plot_canvas):
            widget=c.get_tk_widget();w,h=widget.winfo_width(),widget.winfo_height()
            if min(w,h)>10 and (abs(c.figure.bbox.width-w)>.5 or abs(c.figure.bbox.height-h)>.5):
                c.figure.set_size_inches(w/c.figure.dpi,h/c.figure.dpi,forward=False);c.draw_idle()

    def _render_form(self,cat):
        form=self.forms[cat];p=form.body
        for key,w in list(self.texts.items()):
            if w.frame.master is p and w.winfo_exists():
                self.measurements[key]=w.get("1.0","end-1c");del self.texts[key]
        for child in p.winfo_children():child.destroy()
        form.canvas.yview_moveto(0)
        mode=self._v("test_"+cat)
        if cat=="Beam":
            if mode=="kVp":
                self._hint(p,"Paste two columns: set kVp, measured kVp. Use one row per reading and repeat the set kVp for each exposure.")
                self._text_input(p,"kvp")
                self._field(p,"Accuracy max |error| (%)","kvp_accuracy_limit")
                self._field(p,"Repeatability CV limit (%)","kvp_cv_limit")
                self._field(p,"Beam / technique","kvp_beam")
                self._hint(p,"Optional limits. Set and measured kVp are meter-test inputs; the DICOM KVP tag is not a measured value.")
            elif mode=="HVL":
                self._hint(p,"Paste added Al thickness (mm), measured air kerma. Include 0 mm and measurements just above and below 50%.")
                self._text_input(p,"hvl")
                self._field(p,"Kerma unit","hvl_unit","mGy",("mGy","µGy"))
                self._field(p,"Measured kVp (optional)","hvl_kvp")
                self._field(p,"Target / filter","hvl_beam")
                self._field(p,"Compression paddle","paddle","Unspecified",("Unspecified","In beam","Out of beam"))
                self._field(p,"Lower limit (mm Al)","hvl_minimum")
                self._field(p,"Upper limit (mm Al)","hvl_maximum")
            elif mode=="Tube output":
                self._hint(p,"Paste mAs, air kerma (mGy). Use matched kVp, target/filter and geometry for output reproducibility.")
                self._text_input(p,"output")
                self._field(p,"Meter distance (mm)","meter_distance","500")
                self._field(p,"Reference distance (mm)","reference_distance","500")
                self._field(p,"Output CV limit (%)","output_cv_limit")
                self._field(p,"Beam / technique","output_beam")
            else:
                self._hint(p,"2D phantom MGD = K × g × c × s. Enter validated factors and measured incident air kerma without backscatter.")
                for label,key in (("Entrance kerma (mGy)","incident_kerma"),("HVL (mm Al)","mgd_hvl"),
                                  ("PMMA (mm)","pmma_thickness"),("Factor g","factor_g"),("Factor c","factor_c"),
                                  ("Factor s","factor_s"),("Upper limit (mGy)","mgd_limit"),("Factor source / beam","factor_source")):
                    self._field(p,label,key)
                self._button(p,"Use last measured HVL",self._copy_hvl).pack(fill="x",pady=4)
        elif cat=="Image QA":
            self._signal_field(p)
            if mode=="SNR":
                self._hint(p,"Draw one homogeneous Analysis ROI. Offset is in the same units as the selected signal.")
                self._field(p,"Signal offset to subtract","snr_offset","0")
                self._field(p,"SNR minimum (optional)","snr_minimum")
                self._roi_button(p,"Analysis")
            elif mode=="CNR / SDNR":
                self._hint(p,"Draw the Signal ROI in the detail and the Background ROI in the adjacent homogeneous region.")
                self._field(p,"Noise denominator","cnr_noise","Background SD",("Background SD","RMS of both SDs"))
                self._field(p,"CNR minimum (optional)","cnr_minimum")
                self._roi_button(p,"Signal");self._roi_button(p,"Background")
            elif mode=="Ghost factor":
                self._hint(p,"Use the second ghost-test exposure and the numbered regions in EUREF Fig. 2.2. Draw R1, R2 and R3 separately, or enter all three means.")
                self._field(p,"Source","ghost_source","Image ROIs",("Image ROIs","Entered means"))
                for key in ("R1","R2","R3"):
                    self._field(p,f"{key} mean (manual only)","ghost_"+key)
                    self._roi_button(p,key)
                self._field(p,"Max |factor| (optional)","ghost_limit")
            elif mode=="Uniformity":
                self._hint(p,"Use an Analysis ROI on a uniform flat field. Reports global/local signal and SNR nonuniformity.")
                self._field(p,"Tile width (mm)","uniform_tile","10")
                self._field(p,"Overlap (fraction)","uniform_overlap","0.5")
                self._roi_button(p,"Analysis")
            else:
                self._hint(p,"Screen a uniform image for local outliers. The threshold is a detection setting; it is not a pass/fail limit.")
                self._field(p,"Tile width (mm)","defect_tile","10")
                self._field(p,"Deviation threshold (%)","defect_threshold","20")
                self._field(p,"Exclude border (mm)","defect_border","0")
                self._roi_button(p,"Analysis")
        elif mode=="MTF":
            self._signal_field(p)
            self._hint(p,"Draw an Analysis ROI across one slanted edge/slit, with clean margins and a slight angle. Use detector-plane pitch.")
            self._field(p,"Method","mtf_method","edge",("edge","slit"))
            self._field(p,"Feature orientation","feature_orientation","auto",("auto","vertical","horizontal"))
            self._field(p,"Oversampling","mtf_oversampling","10")
            self._field(p,"Profile length (mm)","mtf_profile","8")
            self._field(p,"Slit width correction (mm)","slit_width","0")
            self._roi_button(p,"Analysis")
        elif mode=="NPS / NNPS":
            self._signal_field(p)
            self._hint(p,"Use homogeneous unprocessed flat fields at one exposure and geometry. The active Analysis ROI is used on every selected image.")
            self._field(p,"Images","nps_source","Active image",("Active image","Selected images"))
            self._field(p,"Fragment size (px)","nps_fragment","256")
            self._field(p,"Overlap (fraction)","nps_overlap","0.5")
            self._field(p,"Detrending","nps_detrend","quadratic",("mean","plane","quadratic"))
            self._field(p,"Window","nps_window","none",("none","hann"))
            self._field(p,"Axis lines omitted","nps_omit","1")
            self._field(p,"Lines per side","nps_lines","7")
            self._roi_button(p,"Analysis")
        elif mode=="DQE":
            self._hint(p,"Select completed MTF and NPS runs. The MTF direction determines the NNPS direction. Enter measured detector-plane kerma and beam-specific q.")
            self.mtf_picker=self._field(p,"MTF run","dqe_mtf","",(" ",))
            self.nps_picker=self._field(p,"NPS run","dqe_nps","",(" ",))
            self._update_dqe_pickers()
            self._field(p,"Detector air kerma (µGy)","detector_kerma")
            self._field(p,"q (mm⁻² per µGy)","input_quanta_q")
            self._field(p,"Beam / q source","dqe_beam")
            self._field(p,"Acquisition / signal check","dqe_confirmation","Not confirmed",("Not confirmed","Confirmed"))
            self._hint(p,"Confirm matching detector sampling, suitable linear signal, compatible beam geometry and correct kerma/q. DQE above 100% is flagged.")
        else:
            self._hint(p,"Paste detector air kerma (µGy), mean stored signal. Use at least four exposures with matched geometry.")
            self._text_input(p,"response")
            self._field(p,"Response model","response_model","Linear",coq.MODELS[1:])
            self._field(p,"Kerma to pair with ROI (µGy)","response_kerma")
            self._button(p,"Append active ROI mean",self.append_response).pack(fill="x",pady=4)
            ttk.Label(p,textvariable=self.calibration_status,wraplength=340).pack(fill="x",pady=(3,6))
            self._hint(p,"After fitting, select Fitted calibration in MTF/NPS. Use the same calibration for both measurements.")

    def _signal_field(self,p):
        row=ttk.Frame(p);row.pack(fill="x",pady=2)
        ttk.Label(row,text="Signal for analysis",width=26).pack(side="left")
        ttk.Combobox(row,state="readonly",textvariable=self.signal_mode,values=("Stored signal","Fitted calibration"),
                     width=17).pack(side="left",fill="x",expand=True)

    def _roi_button(self,p,role):
        self._button(p,f"Select {role} ROI tool",lambda:self._choose_role(role)).pack(fill="x",pady=2)

    def _choose_role(self,role):
        self.role.set(role);self._roi_entries()
        self.status.set(f"Drag a rectangle in the viewer to set the {role} ROI.")

    def _update_dqe_pickers(self):
        for attr,test,key in (("mtf_picker","MTF","dqe_mtf"),("nps_picker","NPS / NNPS","dqe_nps")):
            w=getattr(self,attr,None)
            if w is None or not w.winfo_exists():continue
            choices=[f"{r.id} | {r.label or r.test}" for r in self.results if r.test==test]
            w.configure(values=choices)
            if self._v(key) not in choices:self.vars[key].set(choices[-1] if choices else "")

    def _copy_hvl(self):
        recent=next((r for r in reversed(self.results) if r.test=="HVL"),None)
        if recent is None:raise ValueError("Calculate an HVL first.")
        self.vars["mgd_hvl"].set(reports.display(recent.metrics[0].value))

    def _start(self,label,action,done):
        if self.busy:raise ValueError("Wait for the current operation to finish.")
        self.busy=True;self.status.set(label)
        def task():
            try:self.events.put((done,action(),None))
            except Exception as exc:self.events.put((None,None,exc))
        threading.Thread(target=task,daemon=True).start()

    def _poll(self):
        if self.closing:return
        try:done,result,error=self.events.get_nowait()
        except queue.Empty:pass
        else:
            self.busy=False
            if error:self._guard(lambda:(_ for _ in ()).throw(error))
            else:self._guard(lambda:done(result))
        self.root.after(75,self._poll)

    def open_dicom(self):
        if self.busy:raise ValueError("Wait for the current operation.")
        files=filedialog.askopenfilenames(parent=self.root,title="Open detector DICOM",
                        filetypes=[("DICOM files","*.dcm *.ima *.dicom"),("All files","*")])
        if not files:return
        frame=qa.number(self.frame.get(),"DICOM frame",minimum=1)
        if not frame.is_integer():raise ValueError("DICOM frame must be a whole number.")
        index=int(frame)-1;rescale=self.dicom_lut.get()
        def decode():
            images=[];errors=[]
            for f in files:
                try:images.append(qa.load_dicom(f,index,rescale))
                except Exception as exc:errors.append(f"{Path(f).name}: {exc}")
            return images,errors
        def done(result):
            images,errors=result
            for record in images:self._add_image(record)
            if images:self._refresh_images(len(self.images)-len(images))
            self.status.set(f"Loaded {len(images)} image(s)."+(f" {len(errors)} could not be opened." if errors else ""))
            if errors:messagebox.showwarning("DICOM import","\n\n".join(errors[:6]),parent=self.root)
        self._start("Decoding DICOM image(s)…",decode,done)

    def _add_image(self,image,label=None,rois=None):
        self.image_serial+=1
        image.pixels.setflags(write=False)
        obj=Loaded(image,label or f"Image {self.image_serial:02d}",dict(rois or {}))
        self.images.append(obj);return obj

    def _refresh_images(self,active=0):
        self.image_list.delete(0,"end")
        for item in self.images:self.image_list.insert("end",f"{item.label}  |  {item.image.name}")
        self.active=min(active,len(self.images)-1) if self.images else None
        if self.active is not None:
            self.image_list.selection_set(self.active);self.image_list.activate(self.active);self.image_list.see(self.active)
        self.window=None;self._roi_entries();self._render_image()

    def choose_image(self,_event=None):
        selected=self.image_list.curselection()
        if not selected:return
        candidate=self.image_list.index("active")
        if candidate not in selected:candidate=selected[-1]
        if candidate!=self.active:
            self.active=candidate;self.window=None;self._roi_entries();self._render_image()

    def _item(self):
        if self.active is None:raise ValueError("Open or select a detector image.")
        return self.images[self.active]

    def remove_image(self):
        if self.busy:raise ValueError("Wait for the current operation.")
        self._item()
        ix=self.active;del self.images[ix];self._refresh_images(ix)
        self.status.set("Image removed. Recorded measurements remain available.")

    def image_info(self):
        item=self._item();md=item.image.metadata
        details=[f"{item.label}: {item.image.name}",f"Shape (rows, cols): {item.image.pixels.shape}",
                 f"Active pixel pitch (dy, dx) mm: {item.spacing}"]
        details.extend(f"{k}: {v}" for k,v in md.items())
        messagebox.showinfo("Image acquisition", "\n".join(details),parent=self.root)

    def pixel_pitch(self):
        item=self._item();initial=" ".join(str(x) for x in item.spacing) if item.spacing else ""
        s=simpledialog.askstring("Detector pixel pitch","Enter row and column spacing in mm, separated by a space.\n"
                  "Leave empty to restore the DICOM spacing.",initialvalue=initial,parent=self.root)
        if s is None:return
        if not s.strip():item.override=None
        else:
            parts=s.split()
            if len(parts)!=2:raise ValueError("Enter exactly two values, for example 0.085 0.085.")
            item.override=coq.spacing2d(tuple(qa.number(v,"pixel spacing") for v in parts))
        self._render_image();self.status.set(f"Active spacing for {item.label}: {item.spacing} mm.")

    def _render_image(self):
        if self.selector is not None:
            self.selector.set_active(False);self.selector.disconnect_events();self.selector=None
        ax=self.image_ax;ax.clear();self.roi_artists=[]
        if self.active is None:
            ax.set_facecolor("#EDF3F5")
            ax.text(.5,.53,"Open a detector image",transform=ax.transAxes,ha="center",va="center",
                    color=reports.INK,fontsize=15,fontweight="bold")
            ax.text(.5,.43,"DICOM viewer and measurement ROIs",transform=ax.transAxes,ha="center",
                    color=reports.MUTED,fontsize=10);ax.set_axis_off()
            self.image_title.set("No image loaded")
        else:
            obj=self._item();im=obj.image;a=im.pixels;h,w=a.shape
            step=max(1,math.ceil(max(h,w)/1450));preview=a[::step,::step]
            if self.window is None:
                lo,hi=np.percentile(preview,[.6,99.4]);hi=max(hi,lo+1)
            else:lo,hi=self.window
            ax.imshow(preview,cmap="gray_r" if im.metadata.get("PhotometricInterpretation")=="MONOCHROME1" else "gray",
                      vmin=lo,vmax=hi,interpolation="nearest",extent=(-.5,w-.5,h-.5,-.5))
            ax.set(xlabel="Column (px)",ylabel="Row (px)");ax.tick_params(labelsize=8)
            pitch=(" × ".join(f"{v:g}" for v in obj.spacing)+" mm") if obj.spacing else "pitch missing"
            intent=str(im.metadata.get("PresentationIntentType","")).upper()
            self.image_title.set(f"{obj.label}  ·  {w} × {h} px  ·  {pitch}"+("  ·  PRESENTATION" if "PRESENTATION" in intent else ""))
            self.selector=RectangleSelector(ax,self._on_drag,useblit=False,button=[1],minspanx=1,minspany=1,
                                           spancoords="data",interactive=False,
                                           props=dict(facecolor="#21DDCC",edgecolor="#21DDCC",alpha=.22))
            self._overlay()
        self.toolbar.update();self.image_canvas.draw_idle()

    def _overlay(self):
        for a in self.roi_artists:a.remove()
        self.roi_artists=[]
        if self.active is not None:
            for key,(x,y,w,h) in self._item().rois.items():
                c=ROLES[key]
                box=Rectangle((x-.5,y-.5),w,h,fill=False,edgecolor=c,linewidth=1.35)
                self.image_ax.add_patch(box)
                t=self.image_ax.text(x,y+2,key,color=c,fontsize=8,weight="bold",va="top",clip_on=True,
                                    bbox=dict(facecolor="#16242E",alpha=.64,pad=2,edgecolor="none"))
                self.roi_artists.extend((box,t))
        self.image_canvas.draw_idle()

    def _roi_entries(self):
        values=self._item().rois.get(self.role.get(),("","","","")) if self.active is not None else ("","","","")
        for v,value in zip(self.roi_fields.values(),values):v.set(str(value))

    def _on_drag(self,press,release):
        if self.active is None or None in (press.xdata,press.ydata,release.xdata,release.ydata):return
        h,w=self._item().image.pixels.shape
        x0,x1=sorted([press.xdata,release.xdata]);y0,y1=sorted([press.ydata,release.ydata])
        x=max(0,int(math.floor(x0+.5)));y=max(0,int(math.floor(y0+.5)))
        endx=min(w,int(math.ceil(x1+.5)));endy=min(h,int(math.ceil(y1+.5)))
        if endx-x>=2 and endy-y>=2:
            self._put_roi((x,y,endx-x,endy-y))
        if self.selector:self.selector.clear()

    def _put_roi(self,r):
        item=self._item()
        qa.subregion(item.image.pixels,r)
        item.rois[self.role.get()]=r
        self._roi_entries();self._overlay()
        self.status.set(f"{item.label} · {self.role.get()} ROI: x={r[0]}, y={r[1]}, {r[2]} × {r[3]} px.")

    def set_roi(self):
        nums=[qa.number(v.get(),k,minimum=0) for k,v in self.roi_fields.items()]
        if not all(v.is_integer() for v in nums):raise ValueError("ROI coordinates and size must be integers.")
        self._put_roi(tuple(map(int,nums)))

    def full_roi(self):
        h,w=self._item().image.pixels.shape;self._put_roi((0,0,w,h))

    def clear_roi(self):
        self._item().rois.pop(self.role.get(),None);self._roi_entries();self._overlay()

    def auto_window(self):
        self._item();self.window=None;self._render_image()

    def set_window(self):
        item=self._item()
        lo,hi=self.window or np.percentile(item.image.pixels,[1,99])
        s=simpledialog.askstring("Display window","Enter minimum and maximum stored values, separated by a space.",
                                   initialvalue=f"{lo:.5g} {hi:.5g}",parent=self.root)
        if s is None:return
        parts=s.split()
        if len(parts)!=2:raise ValueError("Enter exactly two display values.")
        lo,hi=map(float,parts)
        if not np.isfinite([lo,hi]).all() or hi<=lo:raise ValueError("Display maximum must exceed minimum.")
        self.window=lo,hi;self._render_image()

    def _analysis_roi(self,item):
        if "Analysis" not in item.rois:raise ValueError("Draw an Analysis ROI, or use Full to select the whole image.")
        return item.rois["Analysis"]

    def _calibration(self):
        if self.signal_mode.get()=="Stored signal":return None
        if self.response.model=="Identity":raise ValueError("Fit a response calibration first, or choose Stored signal.")
        return self.response

    def _spacing(self,item):
        if item.spacing is None:raise ValueError("Detector pixel pitch is missing. Use Pixel pitch to enter measured row and column spacing.")
        return item.spacing

    def _context(self,items,roi,calibration):
        sources=[]
        for i in items:
            md=i.image.metadata
            parts=[f"{i.label}, frame {md.get('Frame',1)}"]
            for k in ("KVP","Exposure","ExposureInuAs","AnodeTargetMaterial","FilterMaterial","BodyPartThickness","PresentationIntentType"):
                if k in md:parts.append(f"{k}={md[k]}")
            sources.append(" / ".join(parts))
        context={"Acquisition": " | ".join(sources),
                 "Pixel spacing (dy, dx) mm":items[0].spacing,
                 "Pixel pitch source":"User override" if items[0].override else items[0].image.metadata.get("Spacing source","Unspecified"),
                 "Signal calibration":asdict(calibration) if calibration is not None else "Stored signal",
                 "DICOM modality transform":any(i.image.metadata.get("Modality transform applied") for i in items)}
        if roi is not None:context["Analysis ROI (x, y, w, h)"]=roi
        return context

    def _record_image(self,result,ctx,images):
        result.inputs.update(ctx)
        if any("PRESENTATION" in str(i.image.metadata.get("PresentationIntentType","")).upper()
                or str(i.image.metadata.get("LossyImageCompression",""))=="01" for i in images):
            result.notes.append("A source is tagged for presentation or lossy compression. Check suitability for quantitative QA.")
        return result

    def _matched_flats(self,items,spacing,shape):
        if any(i.image.pixels.shape!=shape or i.spacing is None or not np.allclose(i.spacing,spacing,rtol=.001,atol=0) for i in items):
            raise ValueError("Selected flat fields must match the active image dimensions and detector pitch.")
        for key in ("KVP","Exposure","ExposureInuAs","AnodeTargetMaterial","FilterMaterial","Modality transform applied"):
            vals={str(i.image.metadata[key]) for i in items if key in i.image.metadata}
            if len(vals)>1:raise ValueError(f"Selected flat fields differ in {key}. Select one matched technique.")

    def calculate(self,cat):
        if self.busy:raise ValueError("Wait for the current operation.")
        test=self._v("test_"+cat);label=self.run_label.get().strip()
        if cat=="Beam":
            if test=="kVp":
                values=self._readings("kvp",("set kVp","measured kVp"))
                acc=self._n("kvp_accuracy_limit",optional=True,minimum=0);cv=self._n("kvp_cv_limit",optional=True,minimum=0)
                beam=self._v("kvp_beam")
                def action():
                    r=qa.voltage(values,acc,cv,label);r.inputs["Beam / technique"]=beam;return r
            elif test=="HVL":
                values=self._readings("hvl",("Al thickness","air kerma"))
                lo=self._n("hvl_minimum",optional=True,minimum=0);hi=self._n("hvl_maximum",optional=True,minimum=0)
                args=(values,lo,hi,label,self._v("hvl_unit"),self._n("hvl_kvp",optional=True,minimum=0),self._v("hvl_beam"),self._v("paddle"))
                action=lambda:qa.hvl(*args)
            elif test=="Tube output":
                args=(self._readings("output",("mAs","mGy")),self._n("meter_distance"),self._n("reference_distance"),
                      self._n("output_cv_limit",optional=True,minimum=0),label,self._v("output_beam"))
                action=lambda:qa.output_repeatability(*args)
            else:
                args=tuple(self._n(k) for k in ("incident_kerma","factor_g","factor_c","factor_s","mgd_hvl","pmma_thickness"))
                limit=self._n("mgd_limit",optional=True,minimum=0);reference=self._v("factor_source")
                action=lambda:qa.glandular_dose(*args,label,limit,reference)
        elif test=="Ghost factor" and self._v("ghost_source")=="Entered means":
            args=tuple(self._n("ghost_"+k) for k in ("R1","R2","R3"))
            limit=self._n("ghost_limit",optional=True,minimum=0)
            action=lambda:qa.ghost(*args,limit=limit,label=label)
        elif test=="Response calibration":
            values=self._readings("response",("detector kerma (µGy)","mean stored signal"))
            model=self._v("response_model")
            def fit():
                k,y=map(np.array,zip(*values));response=coq.fit_response(k,y,model)
                fit_values=response.forward(k);x=np.linspace(k.min(),k.max(),160)
                return response,qa.Result(test,label,[qa.Metric("R²",response.r2),qa.Metric("Model",model),
                    qa.Metric("a",response.a),qa.Metric("b",response.b),qa.Metric("c",response.c)],
                    [qa.Table("Response readings",("Kerma (µGy)","Measured signal","Fitted signal"),list(zip(k,y,fit_values)))],
                    [qa.Chart("Detector response","Detector air kerma (µGy)","Stored signal",
                              [("Measured",k,y),("Fit",x,response.forward(x))],marker="o")],
                    ["Apply this inverse response to matched acquisitions. Fit R² does not establish validity outside the calibration range."],
                    {"Calibration":asdict(response)})
            def done(payload):
                self.response,r=payload;self.signal_mode.set("Fitted calibration")
                self.calibration_status.set(f"{self.response.model}, R²={self.response.r2:.6f}")
                self.add_result(r)
            return self._start("Fitting detector response…",fit,done)
        elif test=="DQE":
            if self._v("dqe_confirmation")!="Confirmed":raise ValueError("Confirm acquisition and linear signal suitability before calculating DQE.")
            def chosen(key,kind):
                value=self._v(key)
                if not value:raise ValueError(f"Calculate and select a {kind} result.")
                ix=int(value.split("|",1)[0])
                return next(r for r in self.results if r.id==ix and r.test==kind)
            mtf=chosen("dqe_mtf","MTF");nps=chosen("dqe_nps","NPS / NNPS")
            kerma=self._n("detector_kerma");q=self._n("input_quanta_q");beam=self._v("dqe_beam")
            action=lambda:qa.detector_dqe(mtf,nps,kerma,q,beam=beam,label=label)
        else:
            action=self._image_calculation(test,label)
        self._start(f"Calculating {test}…",action,self.add_result)

    def _image_calculation(self,test,label):
        item=self._item();rois=dict(item.rois);cal=self._calibration()
        if test in ("SNR","CNR / SDNR","Ghost factor"):
            key={"SNR":"snr_minimum","CNR / SDNR":"cnr_minimum","Ghost factor":"ghost_limit"}[test]
            limit=self._n(key,optional=True,minimum=0)
            offset=self._n("snr_offset") if test=="SNR" else 0.
            noise=self._v("cnr_noise") if test=="CNR / SDNR" else "Background SD"
            ctx=self._context([item],None,cal);pitch=item.spacing
            def action():
                r=qa.roi_quality(test,item.image.pixels,rois,spacing=pitch,offset=offset,noise=noise,
                                 limit=limit,label=label,transform=cal.inverse if cal else None)
                return self._record_image(r,ctx,[item])
            return action
        roi=self._analysis_roi(item);sp=self._spacing(item)
        items=[item]
        if test=="NPS / NNPS" and self._v("nps_source")=="Selected images":
            selection=self.image_list.curselection()
            if not selection:raise ValueError("Select flat field images in the acquisition list.")
            items=[self.images[ix] for ix in selection]
            self._matched_flats(items,sp,item.image.pixels.shape)
        ctx=self._context(items,roi,cal)
        def arrays():
            crops=[qa.subregion(i.image.pixels,roi) for i in items]
            return [cal.inverse(a) for a in crops] if cal else crops
        if test=="MTF":
            kwargs={"method":self._v("mtf_method"),"orientation":self._v("feature_orientation"),
                    "oversampling":self._integer("mtf_oversampling"),"profile":self._n("mtf_profile"),
                    "slit_width":self._n("slit_width",minimum=0),"label":label}
            action=lambda:qa.spatial_mtf(arrays()[0],sp,**kwargs)
        elif test=="NPS / NNPS":
            kwargs={"fragment":self._integer("nps_fragment"),"overlap":self._n("nps_overlap"),
                    "detrend":self._v("nps_detrend"),"window":self._v("nps_window"),
                    "omit":self._integer("nps_omit"),"lines":self._integer("nps_lines"),"label":label}
            action=lambda:qa.noise_spectrum(arrays(),sp,**kwargs)
        elif test=="Uniformity":
            tile=self._n("uniform_tile");overlap=self._n("uniform_overlap")
            def action():
                z=coq.uniformity(arrays()[0],sp,tile_mm=tile,overlap=overlap)
                names=(("Local signal","LSNU_percent"),("Global signal","GSNU_percent"),
                       ("Local SNR","LSNRNU_percent"),("Global SNR","GSNRNU_percent"))
                return qa.Result(test,label,[qa.Metric(a+" nonuniformity",z[b],"%") for a,b in names]+
                    [qa.Metric("ROI coverage",z["coverage_percent_of_selected_area"],"%"),qa.Metric("Tiles",z["roi_count"])],
                    inputs={"Tile (mm)":tile,"Overlap":overlap},
                    notes=["Local nonuniformity compares each interior tile to its eight neighbours. "
                           "Global nonuniformity is 200 × (max − min)/(max + min)."],
                    arrays={"Signal map":z["signal_map"],"SNR map":z["snr_map"]})
        else:
            tile=self._n("defect_tile");threshold=self._n("defect_threshold");border=self._n("defect_border",minimum=0)
            def action():
                out=coq.bad_pixels(arrays()[0],sp,tile_mm=tile,threshold_percent=threshold,margin_mm=border)
                xy=out["coordinates_xy"]+np.array(roi[:2])
                return qa.Result(test,label,[qa.Metric("Flagged pixels",len(xy)),qa.Metric("Pixels tested",out["tested_pixels"]),
                    qa.Metric("Tiles skipped (zero mean)",out["zero_mean_tiles_skipped"])],
                    [qa.Table("Flagged pixel coordinates",("x (px)","y (px)"),list(map(tuple,xy)))],
                    inputs={"Tile (mm)":tile,"Deviation threshold (%)":threshold,"Border (mm)":border},
                    notes=["Local outlier screen against each tile mean. Pixel coordinates refer to the original image."])
        return lambda:self._record_image(action(),ctx,items)

    def append_response(self):
        item=self._item();r=self._analysis_roi(item)
        k=coq.positive(self._n("response_kerma"),"Detector kerma")
        mu=float(qa.subregion(item.image.pixels,r).mean())
        w=self.texts["response"]
        if w.get("1.0","end-1c").strip():w.insert("end","\n")
        w.insert("end",f"{k:.8g}, {mu:.10g}")
        self.status.set("Added the active stored ROI mean to the response data.")

    def add_result(self,result):
        self.result_serial+=1;result.id=self.result_serial
        self.results.append(result)
        statuses={m.status for m in result.metrics if m.status}
        status="FAIL" if "FAIL" in statuses else "Partial" if "Not assessed" in statuses and "PASS" in statuses else (
               "PASS" if "PASS" in statuses else "Not assessed" if statuses else "Measured")
        metric=result.metrics[0]
        self.tree.insert("","end",iid=str(result.id),values=(result.id,result.test,result.label,metric.label,
                         (reports.display(metric.value)+" "+metric.unit).strip(),status),tags=(status,))
        self.tree.selection_set(str(result.id));self.tree.see(str(result.id))
        self.current=result;self._show_result()
        self.results_tabs.select(2 if result.charts else 1)
        self.measurement_count.set(f"{len(self.results)} result sets");self._update_dqe_pickers()
        self.status.set(f"{result.test} recorded as result {result.id:02d} · {status}.")

    def choose_result(self,_event=None):
        selected=self.tree.selection()
        if selected:
            r=next((x for x in self.results if str(x.id)==selected[0]),None)
            if r is not self.current:
                self.current=r;self._show_result()

    def _show_result(self):
        r=self.current
        self.result_title.set(f"Result {r.id:02d}  ·  {r.test}" if r else "Results")
        self.detail.configure(state="normal");self.detail.delete("1.0","end")
        if r:
            data=[f"{r.test}   |   {r.label or 'QA measurement'}   |   {r.timestamp}",""]
            for m in r.metrics:
                value=reports.display(m.value) if m.value is not None else "—"
                suffix=f"   [{m.criterion}; {m.status}]" if m.criterion else ""
                data.append(f"{m.label}: {value} {m.unit}{suffix}".rstrip())
            data += ["","INPUTS AND METHOD"]+[f"{k}: {reports.display(v)}" for k,v in r.inputs.items()]
            data += [""]+r.notes
            for t in r.tables:
                data += ["",f"{t.title}  ({len(t.rows)} rows)"," | ".join(t.columns)]
                data += [" | ".join(reports.display(v) for v in row) for row in t.rows[:12]]
                if len(t.rows)>12:data.append("… complete table available in the exports.")
            self.detail.insert("end","\n".join(data))
        self.detail.configure(state="disabled")
        labels=[c.title for c in r.charts] if r else []
        if r:
            labels.extend(k for k in ("nps2d","nnps2d","Signal map","SNR map") if k in r.arrays)
        self.plot_picker.configure(values=labels);self.plot_choice.set(labels[0] if labels else "")
        self._draw_plot()

    def _draw_plot(self):
        self.plot_fig.clear();ax=self.plot_fig.add_subplot(111)
        r=self.current;key=self.plot_choice.get()
        chart=next((c for c in r.charts if c.title==key),None) if r else None
        if chart:
            reports.draw_chart(ax,chart)
        elif r and key in r.arrays:
            a=r.arrays[key]
            if key in ("nps2d","nnps2d"):
                axis=r.arrays["frequency_axis"];df=axis[1]-axis[0]
                b=np.log10(np.maximum(a,np.finfo(float).tiny))
                # Ignore the DC bin in the display range, without changing the exported spectrum.
                im=ax.imshow(b,origin="lower",cmap="viridis",extent=[axis[0]-df/2,axis[-1]+df/2]*2,
                             vmin=np.percentile(b,2),vmax=np.percentile(b,98))
                ax.set(xlabel="fx (lp/mm)",ylabel="fy (lp/mm)",title=f"log10 {key.upper()}")
            else:
                im=ax.imshow(a,cmap="viridis",interpolation="nearest");ax.set_title(key)
                ax.set(xlabel="Tile column",ylabel="Tile row")
            self.plot_fig.colorbar(im,ax=ax,pad=.02)
        else:
            ax.text(.5,.5,"No plot for this result",transform=ax.transAxes,ha="center",va="center",color=reports.MUTED)
            ax.set_axis_off()
        self.plot_canvas.draw_idle()

    def remove_result(self):
        if self.busy:raise ValueError("Wait for the current operation.")
        if self.current is None:raise ValueError("Choose a result first.")
        r=self.current;self.results.remove(r);self.tree.delete(str(r.id))
        self.current=self.results[-1] if self.results else None
        if self.current:self.tree.selection_set(str(self.current.id))
        self._show_result();self._update_dqe_pickers();self.measurement_count.set(f"{len(self.results)} result sets")
        self.status.set(f"Removed result {r.id:02d}.")

    def _session(self):
        keys={"Facility":"facility","System / serial":"system","Operator":"operator","QA date":"qa_date",
              "Protocol / revision":"protocol","Meter / calibration ID":"meter","Session notes":"session_notes"}
        return {k:self._v(v) for k,v in keys.items()}

    def export_report(self):
        if not self.results:raise ValueError("Calculate at least one QA result before exporting.")
        path=filedialog.asksaveasfilename(parent=self.root,title="Export all recorded QA results",
                    initialfile="Mammo_QA_Report",defaultextension=".pdf",
                    filetypes=[("PDF report","*.pdf"),("Excel workbook","*.xlsx"),("Excel 97–2003","*.xls"),
                               ("CSV long table","*.csv"),("JSON results","*.json")])
        if not path:return
        p=Path(path);ext=p.suffix.lower()
        if ext not in reports.WRITERS:raise ValueError("Choose .pdf, .xlsx, .xls, .csv, or .json.")
        records=list(self.results);meta=self._session()
        self._start(f"Writing {ext[1:].upper()} report…",lambda:reports.WRITERS[ext](p,meta,records),
                    lambda _:self.status.set(f"Exported {p.name} ({len(records)} result sets)."))

    def export_current_table(self):
        r=self.current
        if r is None or not r.tables:raise ValueError("The selected result does not have a detailed data table.")
        tables=r.tables
        i=0
        if len(tables)>1:
            n=simpledialog.askinteger("Select table","\n".join(f"{i+1}. {t.title}" for i,t in enumerate(tables)),
                                      minvalue=1,maxvalue=len(tables),initialvalue=1,parent=self.root)
            if n is None:return
            i=n-1
        name=filedialog.asksaveasfilename(parent=self.root,defaultextension=".csv",filetypes=[("CSV","*.csv")],
                                         initialfile=f"QA_{r.id:02d}_table_{i+1}.csv")
        if name:
            reports.write_table_csv(name,tables[i]);self.status.set(f"Exported {Path(name).name}.")

    def export_plot(self):
        if not self.plot_choice.get():raise ValueError("Choose a result with a plot.")
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension=".png",filetypes=[("PNG","*.png")],
                                         initialfile=f"QA_{self.current.id:02d}_plot.png")
        if path:
            self.plot_fig.savefig(path,dpi=180,facecolor=WHITE);self.status.set(f"Exported {Path(path).name}.")

    def export_npz(self):
        r=self.current
        if r is None or "nps2d" not in r.arrays:raise ValueError("Choose a completed NPS result.")
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension=".npz",filetypes=[("NumPy archive","*.npz")],
                                         initialfile=f"QA_{r.id:02d}_NPS_2D.npz")
        if path:
            np.savez_compressed(path,**{k:r.arrays[k] for k in ("nps2d","nnps2d","frequency_axis")})
            self.status.set(f"Exported {Path(path).name}.")

    def demo(self):
        if self.busy:raise ValueError("Wait for the current operation.")
        if self.images or self.results or any(w.get("1.0","end-1c").strip() for w in self.texts.values()):
            demo_window=MammoQA(tk.Toplevel(self.root))
            demo_window.demo()
            return
        from scipy.special import erf
        rng=np.random.default_rng(77)
        yy,xx=np.indices((640,640));d=xx-320-.058*(yy-320)
        edge=1800+9700*.5*(1+erf(d/(.86*np.sqrt(2))))+rng.normal(0,8,d.shape)
        flat1=10000+rng.normal(0,229,d.shape);flat2=10000+rng.normal(0,230,d.shape)
        ghost=10000+rng.normal(0,45,d.shape)
        ghost[255:460,110:315]-=400;ghost[255:460,330:535]-=365
        sets=[("Slanted edge",edge,{"Analysis":(205,120,230,400)}),
              ("Flat field 1",flat1,{"Analysis":(32,32,576,576)}),
              ("Flat field 2",flat2,{"Analysis":(32,32,576,576)}),
              ("Ghost and contrast",ghost,{"Analysis":(40,60,200,150),"Signal":(135,270,155,155),
                 "Background":(135,60,155,155),"R1":(100,35,200,200),"R2":(112,257,200,200),"R3":(332,257,200,200)})]
        start=len(self.images)
        for name,image,rois in sets:
            self._add_image(coq.ImageRecord(name,image,(.1,.1),{"Demo":True,"Spacing source":"Synthetic",
                            "KVP":"28","Exposure":"40","Modality transform applied":False}),f"Demo {name}",rois)
        self._refresh_images(start);self.signal_mode.set("Stored signal");self._choose_role("Analysis")
        examples={"kvp":"26, 25.9\n26, 26.0\n26, 26.1\n28, 28.1\n28, 28.0\n28, 28.2",
                  "hvl":"0.00, 10.02\n0.30, 5.63\n0.40, 4.65\n0.00, 9.98",
                  "output":"45, 4.18\n45, 4.20\n45, 4.22","response":"10, 2000\n20, 4000\n30, 6000\n40, 8000\n50, 10000"}
        for key,text in examples.items():
            self.measurements[key]=text
            if key in self.texts:
                w=self.texts[key];w.delete("1.0","end");w.insert("1.0",text)
        for key,value in (("detector_kerma","50"),("input_quanta_q","5000"),("dqe_beam","Synthetic demo only"),
                          ("dqe_confirmation","Not confirmed"),("kvp_beam","Synthetic demo only"),
                          ("hvl_beam","Synthetic demo only")):self._var(key,value).set(value)
        self.run_label.set("Synthetic demonstration")
        self.status.set("Demo added. Select slanted edge for MTF, flat fields for NPS, or ghost image for ROI tests.")

    def help(self):
        win=tk.Toplevel(self.root);win.title("Mammo QA Studio | Guide");win.geometry("850x680")
        text=ScrolledText(win,wrap="word",font=("TkDefaultFont",11),padx=12,pady=10)
        text.pack(fill="both",expand=True)
        readme=Path(__file__).with_name("README.md")
        text.insert("end",readme.read_text(encoding="utf-8") if readme.exists() else "Open DICOM images, draw ROIs and select a test.")
        text.configure(state="disabled")

    def _close(self):
        if self.busy:
            messagebox.showinfo("Operation running","Wait until the current calculation or export completes.",parent=self.root);return
        self.closing=True;self.root.destroy()


def main():
    parser=argparse.ArgumentParser(description="Mammo QA Studio desktop application")
    parser.add_argument("--demo",action="store_true",help="Load synthetic test images at startup")
    args=parser.parse_args()
    root=tk.Tk();app=MammoQA(root)
    if args.demo:app.demo()
    root.mainloop()


if __name__=="__main__":
    main()
