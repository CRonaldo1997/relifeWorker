(function(){
  function init(){
    var el = document.getElementById('mascot-lion');
    if (!el || el.__bound) return;
    el.__bound = true;
    var greetTimer = null;
    function greet(){
      el.classList.add('is-greeting');
      if (greetTimer) clearTimeout(greetTimer);
      greetTimer = setTimeout(function(){ el.classList.remove('is-greeting'); }, 1900);
    }
    el.addEventListener('click', greet);
    el.addEventListener('keydown', function(e){
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); greet(); }
    });
    // Public hook so other modules can reflect agent state on the mascot:
    //   window.mascotSetState('idle' | 'thinking' | 'tool' | 'error');
    window.mascotSetState = function(state){
      var allowed = {idle:1, thinking:1, tool:1, error:1};
      el.dataset.state = allowed[state] ? state : 'idle';
    };
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();