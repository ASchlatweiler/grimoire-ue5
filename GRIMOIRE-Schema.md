# **GRIMOIRE — Current Schema Specification (V1/V2 Implementation State)**

## **Overview**

This document defines the **actual implemented schema** for GRIMOIRE as of the current development state (post-V1, mid-V2).

It reflects:

* **Real extraction capabilities**

* **Normalized output shapes**

* **Known deviations from the original design document**

* **Recommended structural refinements**

GRIMOIRE prioritizes **legibility, consistency, and reasoning usability** over maximal data completeness.

---

## **1\. Design Intent vs Implementation Reality**

The original design document defines an ideal schema including:

* component hierarchies

* variable defaults

* exposure flags

* deeper graph inspection

However, the current implementation reflects:

**The maximum stable, reliable introspection surface available via Unreal Python APIs**

### **Key Differences**

| Area | Design Doc | Current Implementation |
| ----- | ----- | ----- |
| Components | Parent hierarchy | `scs_node` identifier |
| Variables | name, type, default, exposed | `{name, type}` only |
| Functions | structured graph | `body: string[]` summaries |
| Graph Data | out-of-scope (V1) | partial via summaries / topology |

---

## **2\. Core Principles (Implementation-Level)**

* **Read-Only**: No mutation of UE5 project state

* **Deterministic Output**: Same query → same structure

* **Schema Stability \> Completeness**

* **Structured for Reasoning, not Rendering**

* **Graceful Degradation**: Partial data is allowed, never crashes

* **Errors are Structured, Not Exceptions**

---

## **3\. Tool Response Schemas**

### **3.1 ping**

{"ok": true, "pong": true}  
---

### **3.2 list\_blueprints**

\[  
 {"name": "BP\_MyActor", "path": "/Game/Blueprints/BP\_MyActor", "class": "Blueprint"}  
\]  
---

### **3.3 get\_blueprint**

{  
 "name": "BP\_MyActor",  
 "path": "/Game/Blueprints/BP\_MyActor",  
 "parent\_class": "Actor",  
 "interfaces": \["BPI\_Interactable"\],

 "components": \[  
   {  
     "name": "RootComponent",  
     "class": "SceneComponent",  
     "scs\_node": "SCS\_Node\_RootComponent"  
   }  
 \],

 "variables": \[  
   {"name": "bIsActive", "type": "BoolProperty"},  
   {"name": "Health", "type": "FloatProperty"}  
 \],

 "functions": \[  
   {  
     "name": "Interact",  
     "outputs": \[  
       {"name": "Result", "type": "bool"}  
     \],  
     "body": \[  
       "get CanInteract",  
       "call Interact\_Implementation",  
       "return Result (bool) \<- ?"  
     \]  
   }  
 \],

 "warnings": \[\]  
}  
---

### **3.4 list\_components**

\[  
 {  
   "name": "RootComponent",  
   "class": "SceneComponent",  
   "scs\_node": "SCS\_Node\_RootComponent"  
 }  
\]  
---

### **3.5 get\_variables**

\[  
 {"name": "bIsActive", "type": "BoolProperty"},  
 {"name": "Health", "type": "FloatProperty"}  
\]  
---

### **3.6 list\_interfaces**

\[  
 {  
   "name": "BPI\_Interactable",  
   "path": "/Game/Interfaces/BPI\_Interactable",  
   "functions": \["Interact", "CanInteract"\]  
 }  
\]  
---

### **3.7 get\_interface**

{  
 "name": "BPI\_Interactable",  
 "functions": \[  
   {  
     "name": "Interact",  
     "inputs": \[  
       {"name": "Instigator", "type": "Object"}  
     \],  
     "outputs": \[  
       {"name": "Result", "type": "bool"}  
     \]  
   }  
 \]  
}  
---

### **3.8 find\_event\_bindings**

{  
 "source": "live\_scan",  
 "event\_bindings": {},  
 "interface\_implementations": {  
   "BPI\_Interactable": \[  
     {"blueprint": "BP\_Door", "implementationType": "Full"}  
   \]  
 },

 "warnings": \[\]  
}  
---

### **3.9 asset\_search**

\[  
 {  
   "name": "BP\_MyActor",  
   "path": "/Game/Blueprints/BP\_MyActor",  
   "assetClass": "Blueprint",  
   "size": 0  
 }  
\]  
---

## **4\. Error Schema**

All errors follow a consistent structured format:

{  
 "error": true,  
 "type": "VALIDATION | TRANSPORT | RUNTIME",  
 "code": "EDITOR\_OFFLINE | TIMEOUT | ASSET\_NOT\_FOUND | HANDLER\_ERROR",  
 "message": "Human-readable description",  
 "tool": "get\_blueprint"  
}

* Errors are always returned as **tool results**, never as protocol-level failures

* This ensures consistent handling across all consumers

---

## **5\. Debug Behavior (Implemented)**

**Implemented.** Domain arrays are no longer polluted with `{"debug_error": "..."}`. Failures are reported in a top-level `warnings` array:

{  
 "variables": \[...\],  
 "warnings": \[  
   {"code": "PARTIAL\_PARSE", "message": "...", "section": "variables"}  
 \]  
}

`get_blueprint` and `find_event_bindings` both return `warnings`; the latter uses `"blueprint"` instead of `"section"` to identify failing assets.

### **Rationale**

* Avoid mixed-type arrays

* Improve validation

* Improve LLM reasoning clarity

* Future-proof for UI consumption

---

## **6\. Field Semantics**

### **scs\_node**

* Represents the Unreal SCS (Simple Construction Script) node identifier

* Acts as the **closest available stable component identifier**

* Not guaranteed to represent hierarchy directly

---

### **functions.body**

* Ordered list of string summaries

* Represents extracted execution steps

Example:

\[  
 "get CanInteract",  
 "call Interact\_Implementation",  
 "return Result (bool) \<- ?"  
\]

#### **Important:**

* This is a **summary trace**, not a full graph representation

* Not guaranteed to be exhaustive or perfectly ordered

* Intended for reasoning, not execution reconstruction

---

## **7\. Schema Classification**

### **7.1 Core Fields (Stable)**

* `name`

* `path`

* `class`

* `type`

* `interfaces`

* `functions`

---

### **7.2 Best-Effort Fields**

* `scs_node`

* `outputs`

* `body`

These may vary based on extraction capabilities.

---

### **7.3 Diagnostic Fields**

* `warnings` — implemented on `get_blueprint` and `find_event_bindings`; contains `{code, message, section?|blueprint?}`

---

## **8\. Current Capabilities**

GRIMOIRE currently supports:

* Blueprint structure inspection

* Component enumeration

* Variable discovery

* Interface implementation mapping

* Function summary extraction

* Basic event binding detection

* Asset registry search

---

## **9\. Known Limitations**

* No variable default values

* No exposure flags (`isExposed`)

* No full component hierarchy

* No full execution graph reconstruction

* `functions.body` is not authoritative logic

---

## **10\. Recommended Next Steps**

### **High Priority**

* Stabilize schema naming conventions

### **Medium Priority**

* Add variable metadata (defaults, exposure)

* Improve component relationships

* Expand function introspection

### **Future (V3+)**

* Full graph topology exposure

* Execution path tracing

* Behavioral analysis layer

* Cross-system dependency mapping

---

## **11\. Summary**

GRIMOIRE currently provides:

**A stable, structured, read-only inspection surface for UE5 projects that enables reliable LLM reasoning over real project state.**

It is:

* **practical**

* **usable**

* **extensible**

Even with incomplete fields, the schema is already sufficient for:

* audits

* debugging assistance

* structural analysis

* system understanding

---

## **Closing Note**

This schema represents a **real-world convergence point** between:

* Unreal Engine’s exposed APIs

* structured data modeling

* and LLM reasoning requirements

It should be treated as:

**a stable foundation, not a final form**