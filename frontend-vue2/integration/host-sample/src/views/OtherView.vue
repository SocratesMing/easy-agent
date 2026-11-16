<template>
  <div class="host-page">
    <h1>其他业务</h1>
    <p>query: {{ JSON.stringify($route.query) }} hash: {{ $route.hash }}</p>
  </div>
</template>

<script>
export default { name: 'OtherView' }
</script>
