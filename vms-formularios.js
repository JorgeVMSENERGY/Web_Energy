/* vms-formularios.js - cliente compartido del backend serverless de formularios. */
(function (w, d) {
  'use strict';

  var API_ENDPOINTS = {
    staging: 'https://21u4yslvcd.execute-api.us-east-1.amazonaws.com',
    prod: 'https://4acgm0wucl.execute-api.us-east-1.amazonaws.com'
  };

  var MAX_FILES = 3;
  var MAX_FILE_BYTES = 5 * 1024 * 1024;
  var MAX_TOTAL_FILE_BYTES = 10 * 1024 * 1024;
  var startedAt = new WeakMap();
  var turnstileScript;
  // Independent rollout flags. Production activation requires explicit approval.
  var TURNSTILE = {
    'jorgevmsenergy.github.io': { enabled: true, sitekey: '0x4AAAAAAF1Ixp8IY8H5Laik' },
    'vmsenergy.com': { enabled: true, sitekey: '0x4AAAAAAF1Uw3YVAufKwYit' },
    'www.vmsenergy.com': { enabled: true, sitekey: '0x4AAAAAAF1Uw3YVAufKwYit' }
  };

  function loadTurnstile() {
    if (!turnstileScript) {
      turnstileScript = new Promise(function (resolve, reject) {
        var script = d.createElement('script');
        function fail() {
          w.clearTimeout(timer);
          script.remove();
          reject(new Error('Verificacion no disponible. Intenta nuevamente.'));
        }
        var timer = w.setTimeout(fail, 15000);
        w.vmsTurnstileReady = function () { w.clearTimeout(timer); resolve(); };
        script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=vmsTurnstileReady';
        script.async = true;
        script.onerror = fail;
        d.head.appendChild(script);
      }).catch(function (error) {
        turnstileScript = null;
        throw error;
      });
    }
    return turnstileScript;
  }

  async function turnstileToken(form, formType) {
    var config = TURNSTILE[String(w.location.hostname || '').toLowerCase()];
    if (!config || !config.enabled) return null;
    await loadTurnstile();
    var container = d.createElement('div');
    form.appendChild(container);
    var widget;
    try {
      return await new Promise(function (resolve, reject) {
        var timer = w.setTimeout(fail, 120000);
        function fail() {
          w.clearTimeout(timer);
          reject(new Error('No fue posible verificar la solicitud. Intenta nuevamente.'));
        }
        try {
          widget = w.turnstile.render(container, {
            sitekey: config.sitekey,
            action: formType,
            appearance: 'interaction-only',
            'response-field': false,
            callback: function (token) { w.clearTimeout(timer); resolve(token); },
            'error-callback': fail,
            'expired-callback': fail,
            'timeout-callback': fail
          });
        } catch (_error) { fail(); }
      });
    } finally {
      if (widget !== undefined) w.turnstile.remove(widget);
      container.remove();
    }
  }

  function apiBase() {
    var host = String(w.location.hostname || '').toLowerCase();
    if (host === 'vmsenergy.com' || host === 'www.vmsenergy.com') {
      return API_ENDPOINTS.prod;
    }
    if (host === 'jorgevmsenergy.github.io') {
      return API_ENDPOINTS.staging;
    }
    return '';
  }

  // Mensajes en inglés para páginas con <html lang="en"> (/en/). Se traducen al mostrarse,
  // así los textos en español del resto del archivo quedan como están.
  var MESSAGES_EN = {
    'Verificacion no disponible. Intenta nuevamente.': 'Verification unavailable. Please try again.',
    'No fue posible verificar la solicitud. Intenta nuevamente.': 'We could not verify your request. Please try again.',
    'Completa correctamente los campos obligatorios.': 'Please complete all required fields.',
    'Selecciona una modalidad del proyecto.': 'Please select a project type.',
    'Solo se permiten hasta 3 archivos.': 'You can attach up to 3 files.',
    'Cada archivo debe pesar como máximo 5 MiB.': 'Each file must be 5 MiB or smaller.',
    'Los archivos no pueden superar 10 MiB en total.': 'Attachments cannot exceed 10 MiB in total.',
    'No fue posible enviar el formulario.': 'We could not send the form. Please try again or email contacto@vmsenergy.com.',
    'El servidor no generó una sesión válida para los archivos.': 'The server could not prepare the file upload. Please try again.',
    'El servidor no preparó todos los archivos seleccionados.': 'The server could not prepare all selected files. Please try again.',
    'Recibimos correctamente tu información. Te contactaremos pronto.': 'We received your information. We will contact you soon.',
    'El formulario solo está disponible desde el sitio oficial de VMS Energy.': 'This form only works on the official VMS Energy website.',
    'El servidor no confirmó la recepción del formulario.': 'The server did not confirm receipt of the form. Please try again.',
    'Enviando…': 'Sending…'
  };

  function localize(message) {
    if (!message || !/^en\b/i.test(d.documentElement.getAttribute('lang') || '')) return message;
    if (MESSAGES_EN[message]) return MESSAGES_EN[message];
    var file = /^No fue posible cargar el archivo (.+)\.$/.exec(message);
    return file ? 'We could not upload the file ' + file[1] + '.' : message;
  }

  function feedbackNode(form) {
    var selector = form.getAttribute('data-vms-feedback');
    if (selector) return d.querySelector(selector);
    return form.querySelector('[aria-live]');
  }

  function showFeedback(form, message, isError) {
    var node = feedbackNode(form);
    if (!node) return;
    node.textContent = localize(message) || '';
    node.style.color = isError ? '#b91c1c' : '';
    node.style.display = message ? '' : 'none';
  }

  function setBusy(form, busy) {
    form.setAttribute('aria-busy', busy ? 'true' : 'false');
    Array.prototype.forEach.call(
      form.querySelectorAll('button[type="submit"],input[type="submit"]'),
      function (button) {
        button.disabled = busy;
      }
    );
  }

  function validateForm(form) {
    if (!form.checkValidity()) {
      form.reportValidity();
      throw new Error('Completa correctamente los campos obligatorios.');
    }

    var checkboxName = form.getAttribute('data-vms-required-checkbox');
    if (checkboxName) {
      var selector = 'input[type="checkbox"][name="' + checkboxName + '"]';
      var checked = Array.prototype.some.call(
        form.querySelectorAll(selector),
        function (input) { return input.checked; }
      );
      if (!checked) {
        throw new Error('Selecciona una modalidad del proyecto.');
      }
    }
  }

  function formFields(form) {
    var fields = {};
    var data = new FormData(form);

    data.forEach(function (value, key) {
      if (value instanceof File) return;
      if (key.toLowerCase().indexOf('turnstile') !== -1) return;
      if (key === 'privacidad' || key === 'website' || key === '_honey') return;
      if (key.charAt(0) === '_') return;

      var normalized = String(value == null ? '' : value).trim();
      if (Object.prototype.hasOwnProperty.call(fields, key) && normalized) {
        fields[key] = fields[key] ? fields[key] + ', ' + normalized : normalized;
      } else {
        fields[key] = normalized;
      }
    });

    return fields;
  }

  function selectedFiles(form) {
    var files = [];
    Array.prototype.forEach.call(
      form.querySelectorAll('input[type="file"]'),
      function (input) {
        Array.prototype.forEach.call(input.files || [], function (file) {
          files.push(file);
        });
      }
    );
    return files;
  }

  function validateFiles(files) {
    if (files.length > MAX_FILES) {
      throw new Error('Solo se permiten hasta 3 archivos.');
    }

    var total = 0;
    files.forEach(function (file) {
      if (file.size < 1 || file.size > MAX_FILE_BYTES) {
        throw new Error('Cada archivo debe pesar como máximo 5 MiB.');
      }
      total += file.size;
    });

    if (total > MAX_TOTAL_FILE_BYTES) {
      throw new Error('Los archivos no pueden superar 10 MiB en total.');
    }
  }

  async function apiRequest(base, path, payload) {
    var response = await w.fetch(base + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    var result = {};
    try {
      result = await response.json();
    } catch (_error) {
      result = {};
    }

    if (!response.ok) {
      throw new Error(result.message || 'No fue posible enviar el formulario.');
    }
    return result;
  }

  async function uploadAttachments(base, formType, honeypot, files) {
    if (!files.length) {
      return { upload_session_id: null, attachments: [] };
    }

    var presign = await apiRequest(base, '/uploads/presign', {
      form_type: formType,
      website: honeypot,
      files: files.map(function (file) {
        return {
          name: file.name,
          size: file.size,
          content_type: file.type || ''
        };
      })
    });

    if (!presign.upload_session_id || !Array.isArray(presign.uploads)) {
      throw new Error('El servidor no generó una sesión válida para los archivos.');
    }
    if (presign.uploads.length !== files.length) {
      throw new Error('El servidor no preparó todos los archivos seleccionados.');
    }

    var attachments = [];
    for (var index = 0; index < files.length; index += 1) {
      var upload = presign.uploads[index];
      var uploadResponse = await w.fetch(upload.upload_url, {
        method: 'PUT',
        headers: upload.headers || {},
        body: files[index]
      });
      if (!uploadResponse.ok) {
        throw new Error('No fue posible cargar el archivo ' + files[index].name + '.');
      }
      attachments.push({
        name: upload.name,
        key: upload.key,
        size: upload.size,
        content_type: upload.content_type
      });
    }

    return {
      upload_session_id: presign.upload_session_id,
      attachments: attachments
    };
  }

  function trackSuccess(form, formType) {
    w.__vmsLastForm = form;
    if (typeof w.vmsLead === 'function') {
      w.vmsLead('api');
    }
    if (formType === 'careers' && typeof w.vmsPostulacion === 'function') {
      w.vmsPostulacion(form.id || '', 'api');
    }
  }

  function displaySuccess(form) {
    var successSelector = form.getAttribute('data-vms-success');
    var successNode = successSelector ? d.querySelector(successSelector) : null;
    if (successNode) {
      successNode.classList.add(form.getAttribute('data-vms-success-class') || 'is-visible');
      successNode.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    var hideSelector = form.getAttribute('data-vms-hide-on-success');
    var hideNode = hideSelector ? d.querySelector(hideSelector) : null;
    if (hideNode) hideNode.style.display = 'none';
    showFeedback(
      form,
      form.getAttribute('data-vms-success-message') ||
        'Recibimos correctamente tu información. Te contactaremos pronto.',
      false
    );
  }

  async function submitForm(form) {
    var base = apiBase();
    if (!base) {
      throw new Error('El formulario solo está disponible desde el sitio oficial de VMS Energy.');
    }

    validateForm(form);
    var formType = form.getAttribute('data-vms-form-type');
    var honeypotNode = form.querySelector('[name="website"],[name="_honey"]');
    var privacyNode = form.querySelector('[name="privacidad"]');
    var honeypot = honeypotNode ? String(honeypotNode.value || '').trim() : '';
    var files = selectedFiles(form);
    validateFiles(files);

    var uploads = await uploadAttachments(base, formType, honeypot, files);
    // Obtain a fresh single-use token after potentially slow attachment uploads.
    var token = await turnstileToken(form, formType);
    var result = await apiRequest(base, '/submissions', {
      turnstile_token: token,
      form_type: formType,
      website: honeypot,
      elapsed_ms: Math.max(800, Date.now() - (startedAt.get(form) || Date.now() - 800)),
      privacy_accepted: Boolean(privacyNode && privacyNode.checked),
      fields: formFields(form),
      attachments: uploads.attachments,
      upload_session_id: uploads.upload_session_id,
      page_url: w.location.href,
      page_title: d.title,
      language: d.documentElement.lang || 'es-MX'
    });

    if (result.status !== 'received' || !result.submission_id) {
      throw new Error('El servidor no confirmó la recepción del formulario.');
    }

    trackSuccess(form, formType);
    form.reset();
    displaySuccess(form);
  }

  function bindForms() {
    Array.prototype.forEach.call(
      d.querySelectorAll('form[data-vms-form-type]'),
      function (form) { startedAt.set(form, Date.now()); }
    );
  }

  d.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form || !form.matches || !form.matches('form[data-vms-form-type]')) return;

    event.preventDefault();
    event.stopImmediatePropagation();
    if (form.getAttribute('aria-busy') === 'true') return;

    showFeedback(form, 'Enviando…', false);
    setBusy(form, true);
    submitForm(form)
      .catch(function (error) {
        showFeedback(form, error && error.message ? error.message : 'No fue posible enviar el formulario.', true);
      })
      .finally(function () {
        setBusy(form, false);
      });
  }, true);

  if (d.readyState === 'loading') {
    d.addEventListener('DOMContentLoaded', bindForms);
  } else {
    bindForms();
  }
})(window, document);
