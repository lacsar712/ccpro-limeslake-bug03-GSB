import { Application, Controller } from "https://unpkg.com/@hotwired/stimulus@3.2.2/dist/stimulus.js"

const application = Application.start()

class FlashController extends Controller {
  static targets = ["item"]
  connect() {
    window.setTimeout(() => {
      this.itemTargets.forEach((el) => {
        el.style.opacity = "0"
        el.style.transition = "opacity .4s"
      })
    }, 4000)
  }
}

class FormHintController extends Controller {
  static targets = ["status", "hint"]
  static values = { locked: Boolean }
  connect() {
    this.update()
    this.statusTarget?.addEventListener("change", () => this.update())
  }
  update() {
    if (!this.hasHintTarget || !this.hasStatusTarget) return
    if (this.lockedValue || this.statusTarget.value === "drawn") {
      this.hintTarget.textContent =
        "当前池位「已出灰」：峰值温度与池状态均已锁定，不能再修改。"
    } else {
      this.hintTarget.textContent =
        "出灰前请确认最近熟化批次已记录峰值温度且不低于 60℃。"
    }
  }
}

class BoardController extends Controller {
  static targets = ["drawer", "backdrop"]
  static values = { open: Boolean }

  connect() {
    if (this.openValue) this._setOpen(true)
  }

  openDrawer() {
    // Navigation still loads selected pond; keep drawer state consistent on SPA-less click
    this._setOpen(true)
  }

  closeDrawer(event) {
    if (event) event.preventDefault()
    this._setOpen(false)
    const closeLink = event?.currentTarget
    if (closeLink?.href) {
      window.location.href = closeLink.href
    } else if (this.hasBackdropTarget) {
      const base = new URL(window.location.href)
      base.searchParams.delete("pond")
      window.location.href = base.toString()
    }
  }

  _setOpen(open) {
    this.openValue = open
    if (this.hasDrawerTarget) {
      this.drawerTarget.classList.toggle("is-open", open)
      this.drawerTarget.setAttribute("aria-hidden", open ? "false" : "true")
    }
    if (this.hasBackdropTarget) {
      this.backdropTarget.classList.toggle("is-open", open)
    }
  }
}

application.register("flash", FlashController)
application.register("form-hint", FormHintController)
application.register("board", BoardController)
