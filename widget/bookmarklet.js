// Bookmarklet: injects the EKT AI assistant into any page (e.g. the live ekt.kz).
// Replace HOST with the public URL of the backend that serves /widget/widget.js (must be https when used on https://ekt.kz).
// Copy the single line below (starting with "javascript:") into a bookmark's URL field, then click it on ekt.kz.
javascript:(()=>{const HOST='https://HOST';if(window.EktAssistant){window.EktAssistant.open();return}const s=document.createElement('script');s.src=HOST+'/widget/widget.js?'+Date.now();s.dataset.api=HOST;s.dataset.open='1';document.body.appendChild(s)})()
