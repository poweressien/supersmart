import json
import tkinter as tk
from tkinter import messagebox, scrolledtext

from app import run_scan


def run_single_scan() -> None:
    value = entry.get().strip()
    if not value:
        messagebox.showwarning("Input required", "Please enter a value to scan.")
        return

    result = run_scan(value, category=category_var.get(), aggressive=aggressive_var.get())
    output_box.delete("1.0", tk.END)
    output_box.insert(tk.END, json.dumps(result, indent=2, ensure_ascii=False))


root = tk.Tk()
root.title("Entity Intelligence Desktop")
root.geometry("980x760")
root.configure(bg="#0f172a")

frame = tk.Frame(root, bg="#0f172a", padx=18, pady=18)
frame.pack(fill="both", expand=True)

label = tk.Label(frame, text="Search value", bg="#0f172a", fg="white", font=("Segoe UI", 12, "bold"))
label.pack(anchor="w")

entry = tk.Entry(frame, width=100, font=("Segoe UI", 12), bd=2)
entry.pack(fill="x", pady=(8, 12))

row = tk.Frame(frame, bg="#0f172a")
row.pack(fill="x", pady=(0, 12))

category_var = tk.StringVar(value="all")
category_menu = tk.OptionMenu(row, category_var, "all", "person", "phone", "domain", "email", "url", "number")
category_menu.config(bg="#1e293b", fg="white")
category_menu.pack(side="left", padx=(0, 12))

aggressive_var = tk.BooleanVar(value=False)
check = tk.Checkbutton(row, text="Aggressive search", variable=aggressive_var, bg="#0f172a", fg="white", selectcolor="#1e293b")
check.pack(side="left")

scan_button = tk.Button(frame, text="Scan", command=run_single_scan, bg="#22c55e", fg="white", font=("Segoe UI", 11, "bold"), padx=18)
scan_button.pack(anchor="w", pady=(0, 12))

output_box = scrolledtext.ScrolledText(frame, wrap=tk.WORD, width=120, height=28, bg="#111827", fg="#e5e7eb", font=("Segoe UI", 10))
output_box.pack(fill="both", expand=True)

root.mainloop()
