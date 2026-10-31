// Copyright (c) 2026, Frappe Technologies and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mint AI Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), function () {
			frappe.call({
				doc: frm.doc,
				method: "test_connection",
				freeze: true,
				freeze_message: __("Testing AI connection..."),
				callback: function (r) {
					if (r.message && r.message.success) {
						frappe.msgprint({
							title: __("Success"),
							indicator: "green",
							message: r.message.message + "<br><pre>" + JSON.stringify(r.message.response, null, 2) + "</pre>"
						});
					} else {
						frappe.msgprint({
							title: __("Connection Error"),
							indicator: "red",
							message: (r.message ? r.message.message : __("Unknown error"))
						});
					}
				}
			});
		});
	}
});
